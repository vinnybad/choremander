"""Tests for Choremander's static-path and Lovelace resource registration.

These exercise the real registration path - the `bypass_frontend_registration`
fixture is deliberately NOT requested here, because bypassing it is exactly what
this module is meant to cover.
"""

from __future__ import annotations

import json
from typing import Any, Final
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest

from custom_components.choremander import frontend as frontend_module
from custom_components.choremander.const import DOMAIN
from custom_components.choremander.frontend import (
    CARDS,
    FRONTEND_REGISTERED,
    GLOBAL_MODULES,
    URL_BASE,
    _async_get_version,
    _get_version,
    async_ensure_resources_loaded,
    async_register_cards,
    async_register_frontend,
)

MANIFEST_VERSION = "1.1.2"


class FakeResources:
    """Stand-in for Lovelace's ResourceStorageCollection.

    Hand-written rather than a MagicMock so that `async_items()` really returns
    dicts - the production code indexes into them and splits their URLs.
    """

    def __init__(self, items: list[dict[str, Any]] | None = None, loaded: bool = True) -> None:
        self._items = items or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []
        # Mirrors ResourceStorageCollection: `data` stays empty and `async_items()`
        # lies until `async_load()` has pulled the store off disk.
        self.loaded = loaded
        self.load_calls = 0
        self._stored = list(self._items)
        if not loaded:
            self._items = []

    def async_items(self) -> list[dict[str, Any]]:
        return list(self._items)

    async def async_load(self) -> None:
        self.load_calls += 1
        self._items = list(self._stored)
        self.loaded = True

    async def async_create_item(self, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return data

    async def async_update_item(self, item_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        self.updated.append((item_id, updates))
        return updates


class FakeLovelace:
    """Stand-in for LovelaceData."""

    def __init__(
        self, resources: FakeResources | None = None, resource_mode: str | None = None
    ) -> None:
        self.resources = resources
        if resource_mode is not None:
            self.resource_mode = resource_mode


@pytest.fixture
async def http_ready(hass: HomeAssistant) -> None:
    """Set up `frontend` so `hass.http` and the extra-module URL manager exist."""
    assert await async_setup_component(hass, "frontend", {}), (
        "the frontend component is a hard dependency of the registration code"
    )


def _versioned(card: str, version: str = MANIFEST_VERSION) -> str:
    return f"{URL_BASE}/{card}?v={version}"


# ---------------------------------------------------------------------------
# Static path registration
# ---------------------------------------------------------------------------


async def test_static_path_registration_serves_the_bundled_www_directory(
    hass: HomeAssistant, http_ready: None
) -> None:
    """`/choremander` maps onto the integration's own `www/` folder."""
    with patch.object(hass.http, "async_register_static_paths", AsyncMock()) as register:
        await async_register_frontend(hass)

    (configs,) = register.call_args[0]
    assert len(configs) == 1, "exactly one static path should be registered"
    assert configs[0].url_path == URL_BASE == "/choremander"
    assert configs[0].path.endswith("/custom_components/choremander/www"), (
        f"cards must be served from the integration's www/ folder, got {configs[0].path}"
    )
    assert configs[0].cache_headers is False, (
        "cache headers are disabled so a version bump takes effect immediately"
    )


async def test_static_path_is_registered_only_once_across_config_entries(
    hass: HomeAssistant, http_ready: None
) -> None:
    """The `frontend_registered` sentinel makes a second entry a no-op."""
    await async_register_frontend(hass)
    assert hass.data[DOMAIN][FRONTEND_REGISTERED] is True, (
        "the first registration should mark the frontend as registered"
    )

    with patch.object(hass.http, "async_register_static_paths", AsyncMock()) as register:
        await async_register_frontend(hass)

    assert register.call_count == 0, (
        "a second config entry must not re-register the /choremander static path"
    )


async def test_missing_www_directory_bails_out_without_marking_registered(
    hass: HomeAssistant, http_ready: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing `www/` leaves the sentinel unset so registration stays retryable."""
    with patch("custom_components.choremander.frontend.Path") as fake_path:
        fake_path.return_value.parent.__truediv__.return_value.exists.return_value = False
        with patch.object(hass.http, "async_register_static_paths", AsyncMock()) as register:
            await async_register_frontend(hass)

    assert register.call_count == 0, "nothing should be served when www/ is missing"
    assert FRONTEND_REGISTERED not in hass.data.get(DOMAIN, {}), (
        "the registered sentinel must stay unset so a later attempt can retry"
    )
    assert "www directory not found" in caplog.text, (
        "a missing www/ folder should warn loudly - the cards will 404 otherwise"
    )


async def test_registration_adds_the_global_sound_module_as_an_extra_js_url(
    hass: HomeAssistant, http_ready: None
) -> None:
    """The config-flow sound preview module is loaded on every page."""
    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()),
        patch("custom_components.choremander.frontend.add_extra_js_url") as add_url,
    ):
        await async_register_frontend(hass)

    assert GLOBAL_MODULES == ["choremander-config-sounds.js"], (
        "the set of globally-loaded modules changed; update this test deliberately"
    )
    add_url.assert_called_once_with(
        hass, f"{URL_BASE}/choremander-config-sounds.js?v={MANIFEST_VERSION}"
    )


async def test_real_setup_registers_the_static_path_and_the_global_module(
    hass: HomeAssistant, init_integration: ConfigEntry
) -> None:
    """The unbypassed production path, driven by a real config entry setup."""
    assert hass.data[DOMAIN][FRONTEND_REGISTERED] is True, (
        "setting up an entry should register the frontend for real"
    )
    assert _versioned("choremander-config-sounds.js") in hass.data[DATA_EXTRA_MODULE_URL].urls, (
        "the sound preview module should be registered with Home Assistant's "
        "frontend extra-module URL manager"
    )


# ---------------------------------------------------------------------------
# Version / cache busting
# ---------------------------------------------------------------------------


async def test_the_manifest_is_read_off_the_event_loop(hass: HomeAssistant) -> None:
    """`_get_version` opens a file; doing that in the loop makes HA shout.

    Home Assistant logs "Detected blocking call to open ... please create a bug
    report" with a full stack trace on every startup otherwise, which sends
    users here to report something that is ours to fix.
    """
    with patch.object(
        hass, "async_add_executor_job", wraps=hass.async_add_executor_job
    ) as executor:
        assert await _async_get_version(hass) == MANIFEST_VERSION

    executor.assert_called_once_with(_get_version)


def test_get_version_reads_the_integration_manifest() -> None:
    """Cache busting is driven by `manifest.json`, not a hardcoded literal."""
    assert _get_version() == MANIFEST_VERSION, "the cache-busting version must track manifest.json"


@pytest.mark.parametrize(
    ("label", "opener"),
    [
        ("manifest missing", mock_open()),
        ("manifest is not json", mock_open(read_data="not json at all")),
        ("manifest has no version key", mock_open(read_data="{}")),
    ],
)
def test_get_version_falls_back_to_1_0_0_when_the_manifest_is_unusable(
    label: str, opener: Any
) -> None:
    """An unreadable manifest degrades to a version rather than crashing setup."""
    if label == "manifest missing":
        opener.side_effect = FileNotFoundError
    with patch("builtins.open", opener):
        assert _get_version() == "1.0.0", f"expected the 1.0.0 fallback when {label}"


def test_get_version_propagates_a_permission_error() -> None:
    """Only FileNotFoundError and JSONDecodeError are caught, by design."""
    with (
        patch("builtins.open", side_effect=PermissionError("locked")),
        pytest.raises(PermissionError),
    ):
        _get_version()


# ---------------------------------------------------------------------------
# Lovelace resource registration
# ---------------------------------------------------------------------------


async def test_register_cards_creates_one_versioned_resource_per_card(
    hass: HomeAssistant,
) -> None:
    """All eight cards are registered as versioned ES modules, in `CARDS` order."""
    resources = FakeResources()
    hass.data["lovelace"] = FakeLovelace(resources)

    await async_register_cards(hass)

    assert resources.created == [
        {"url": _versioned(card), "res_type": "module"} for card in CARDS
    ], "the eight cards should be created in CARDS order with a ?v= cache buster"
    assert resources.updated == [], "nothing pre-existed, so nothing should be updated"


async def test_register_cards_updates_only_the_resources_whose_version_is_stale(
    hass: HomeAssistant,
) -> None:
    """A card already at the current version is left completely alone."""
    stale, current, unversioned = CARDS[0], CARDS[1], CARDS[2]
    resources = FakeResources(
        [
            {"id": "stale-id", "url": _versioned(stale, "1.0.0")},
            {"id": "current-id", "url": _versioned(current)},
            {"id": "unversioned-id", "url": f"{URL_BASE}/{unversioned}"},
        ]
    )
    hass.data["lovelace"] = FakeLovelace(resources)

    await async_register_cards(hass)

    assert resources.updated == [
        ("stale-id", {"url": _versioned(stale)}),
        ("unversioned-id", {"url": _versioned(unversioned)}),
    ], (
        "only the outdated resource and the one with no query string should be "
        "rewritten; the already-current resource must not be touched"
    )
    assert [item["url"] for item in resources.created] == [
        _versioned(card) for card in CARDS[3:]
    ], "the two cards that did not exist yet should be created"


# ---------------------------------------------------------------------------
# Regression: issue #2 - registration must not wipe other integrations'
# Lovelace resources.
#
# `ResourceStorageCollection` is lazy: `loaded` starts False, `data` starts
# empty, and `async_items()` reports nothing until `async_load()` runs. Writing
# to it while unloaded makes the debounced `_data_to_save()` serialise only the
# rows Choremander just created - silently truncating `.storage/
# lovelace_resources` to Choremander's own cards and erasing every other one.
# ---------------------------------------------------------------------------

THIRD_PARTY_RESOURCES: Final = [
    {
        "id": "mini-graph-id",
        "url": "/hacsfiles/mini-graph-card/mini-graph-card-bundle.js",
        "type": "module",
    },
    {
        "id": "button-card-id",
        "url": "/hacsfiles/button-card/button-card.js",
        "type": "module",
    },
]


@pytest.fixture
def seeded_lovelace_resources(hass_storage: dict[str, Any]) -> list[dict[str, Any]]:
    """Pre-existing third-party resources on disk, as a real install would have."""
    hass_storage["lovelace_resources"] = {
        "version": 1,
        "minor_version": 1,
        "key": "lovelace_resources",
        "data": {"items": [dict(item) for item in THIRD_PARTY_RESOURCES]},
    }
    return THIRD_PARTY_RESOURCES


async def _flush_delayed_save(hass: HomeAssistant) -> None:
    """Force the debounced `_async_schedule_save` out to the store.

    `Store.async_delay_save` schedules its write with `hass.loop.call_at`, which
    the mocked clock cannot advance, so firing the final-write event - what a
    real shutdown does - is the way to make the pending write land.
    """
    hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
    await hass.async_block_till_done()


async def test_register_cards_loads_the_collection_before_writing_to_it(
    hass: HomeAssistant,
) -> None:
    """An unloaded collection is loaded first, so existing rows are visible."""
    resources = FakeResources(
        [{"id": "third-party-id", "url": "/hacsfiles/button-card/button-card.js"}],
        loaded=False,
    )
    hass.data["lovelace"] = FakeLovelace(resources)

    await async_register_cards(hass)

    assert resources.load_calls == 1, (
        "a lazy resource collection must be loaded before it is written to, or "
        "the save serialises an empty collection and drops every other resource"
    )
    assert [item["url"] for item in resources.async_items()] == [
        "/hacsfiles/button-card/button-card.js"
    ], "loading must not disturb the rows that were already there"


class LoadStatelessResources(FakeResources):
    """A collection with no `loaded` attribute, as a future Home Assistant might.

    Both of Home Assistant's collection classes define `loaded` as a class
    attribute today, so this cannot happen yet - it models the API changing
    under us, which is the one case where guessing wrong destroys user data.
    """

    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        super().__init__(items)
        del self.loaded


async def test_register_cards_refuses_to_write_when_load_state_is_unknowable(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """No `loaded` attribute means no write - silence beats deleting dashboards."""
    resources = LoadStatelessResources(
        [{"id": "third-party-id", "url": "/hacsfiles/button-card/button-card.js"}]
    )
    hass.data["lovelace"] = FakeLovelace(resources)

    await async_register_cards(hass)

    assert resources.created == [], (
        "with no way to tell whether the collection is loaded, writing to it "
        "risks truncating the store - registration must bail out instead"
    )
    assert resources.updated == []
    assert "has no load state" in caplog.text, (
        "the user needs to be told why their cards were not registered"
    )
    assert f"{URL_BASE}/<card-name>.js" in caplog.text, (
        "the warning should say where to find the cards for manual registration"
    )


async def test_ensure_resources_loaded_reports_whether_it_is_safe_to_write(
    hass: HomeAssistant,
) -> None:
    """The shared guard: True once loaded, False when the API is unrecognisable."""
    lazy = FakeResources([{"id": "a", "url": "/local/a.js"}], loaded=False)
    assert await async_ensure_resources_loaded(lazy) is True
    assert lazy.load_calls == 1, "an unloaded collection should be loaded exactly once"
    assert lazy.loaded is True, (
        "async_load() does not set `loaded` itself - the caller must, or every "
        "later write reloads and Home Assistant's own guards stay armed"
    )

    already = FakeResources(loaded=True)
    assert await async_ensure_resources_loaded(already) is True
    assert already.load_calls == 0

    assert await async_ensure_resources_loaded(LoadStatelessResources()) is False


async def test_register_cards_does_not_reload_an_already_loaded_collection(
    hass: HomeAssistant,
) -> None:
    """The `loaded` flag is honoured - no redundant store reads per setup."""
    resources = FakeResources(loaded=True)
    hass.data["lovelace"] = FakeLovelace(resources)

    await async_register_cards(hass)

    assert resources.load_calls == 0, "an already-loaded collection must not be reloaded"
    assert len(resources.created) == len(CARDS)


async def test_real_setup_preserves_third_party_resources_in_memory(
    hass: HomeAssistant,
    seeded_lovelace_resources: list[dict[str, Any]],
    init_integration: ConfigEntry,
) -> None:
    """Issue #2: other integrations' cards survive Choremander's registration."""
    resources = hass.data["lovelace"].resources
    urls = [item["url"] for item in resources.async_items()]

    for item in seeded_lovelace_resources:
        assert item["url"] in urls, (
            f"{item['url']} was erased from the Lovelace resource collection by "
            f"Choremander's card registration; collection now holds {urls}"
        )


async def test_real_setup_does_not_truncate_the_resources_store_on_disk(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    seeded_lovelace_resources: list[dict[str, Any]],
    init_integration: ConfigEntry,
) -> None:
    """The end-to-end symptom: `.storage/lovelace_resources` keeps every row."""
    await _flush_delayed_save(hass)

    saved = [item["url"] for item in hass_storage["lovelace_resources"]["data"]["items"]]

    for item in seeded_lovelace_resources:
        assert item["url"] in saved, (
            f"{item['url']} was written out of .storage/lovelace_resources; "
            f"the file now contains only {saved}"
        )
    assert saved == [item["url"] for item in seeded_lovelace_resources] + [
        _versioned(card) for card in CARDS
    ], "the stored file should be the pre-existing rows followed by Choremander's cards"


async def test_a_restart_neither_duplicates_nor_drops_resources(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    seeded_lovelace_resources: list[dict[str, Any]],
    init_integration: ConfigEntry,
) -> None:
    """Reported symptom was cumulative: re-added resources vanished again on reboot."""
    await _flush_delayed_save(hass)
    after_first_boot = [item["url"] for item in hass_storage["lovelace_resources"]["data"]["items"]]

    # A restart gives Lovelace a fresh, unloaded collection over the same store.
    # Clearing `store._data` is load-bearing: the mocked store short-circuits
    # its load when `_data` is already populated, so without this the reload
    # would be a no-op and the test would pass for the wrong reason.
    collection = hass.data["lovelace"].resources
    collection.data.clear()
    collection.loaded = False
    collection.store._data = None

    await async_register_cards(hass)
    await _flush_delayed_save(hass)

    after_second_boot = [
        item["url"] for item in hass_storage["lovelace_resources"]["data"]["items"]
    ]

    # Assert the third-party rows explicitly. Comparing the two boots against
    # each other is not enough on its own: with the bug present *both* boots
    # truncate to the same eight cards, so the equality below holds happily
    # while the user's resources are gone. This loop is what actually fails.
    for item in seeded_lovelace_resources:
        assert item["url"] in after_second_boot, (
            f"{item['url']} survived the first boot but was erased by the "
            f"second; the store now holds {after_second_boot}"
        )
    assert after_second_boot == after_first_boot, (
        "a restart must leave the resource store byte-identical; "
        f"before={after_first_boot} after={after_second_boot}"
    )


async def test_register_cards_is_a_noop_when_lovelace_is_unavailable(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A YAML-only or storage-less install must not break integration setup."""
    hass.data.pop("lovelace", None)

    await async_register_cards(hass)

    assert "Lovelace not available" in caplog.text, (
        "the user needs to be told the cards require manual registration"
    )


async def test_register_cards_is_a_noop_when_the_resource_collection_is_none(
    hass: HomeAssistant,
) -> None:
    """`LovelaceData.resources` is None before the collection is loaded."""
    lovelace = FakeLovelace(resources=None)
    hass.data["lovelace"] = lovelace

    await async_register_cards(hass)  # must not raise

    assert lovelace.resources is None, (
        "the guard must return early rather than lazily building a collection"
    )


async def test_register_cards_only_logs_instructions_in_yaml_mode(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """In YAML mode the resources are printed for the user to copy, not created."""
    resources = FakeResources()
    hass.data["lovelace"] = FakeLovelace(resources, resource_mode="yaml")

    await async_register_cards(hass)

    assert resources.created == [], "YAML mode must not write to the storage collection"
    assert resources.updated == []
    for card in CARDS:
        assert f"- url: {URL_BASE}/{card}" in caplog.text, (
            f"YAML-mode instructions should list {card} for manual configuration"
        )


async def test_yaml_mode_guard_reads_the_attribute_lovelace_actually_exposes(
    hass: HomeAssistant, init_integration: ConfigEntry
) -> None:
    """The YAML-mode branch is reachable against a real install.

    `frontend.py` used to read `lovelace_data.mode`, which `LovelaceData` does
    not define - so the guard always fell through to the storage path and the
    YAML branch was dead code. It now reads `resource_mode`.
    """
    lovelace_data = hass.data["lovelace"]

    assert hasattr(lovelace_data, "resource_mode"), (
        "LovelaceData should expose resource_mode - that is the attribute "
        "frontend.py reads to decide between the YAML and storage paths"
    )
    assert not hasattr(lovelace_data, "mode"), (
        "LovelaceData grew a `mode` attribute; re-check which one frontend.py should be reading"
    )


async def test_register_cards_never_raises_when_the_resource_api_fails(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A blanket `except Exception` keeps a Lovelace failure from failing setup."""
    resources = MagicMock()
    resources.async_items.side_effect = RuntimeError("resource store exploded")
    hass.data["lovelace"] = FakeLovelace(resources)

    await async_register_cards(hass)  # must not raise

    assert "Could not register Lovelace resources: resource store exploded" in (caplog.text), (
        "a resource-store failure should degrade to a single warning"
    )
    assert f"Cards available at {URL_BASE}/<card-name>.js" in caplog.text, (
        "the warning should tell the user where to find the cards manually"
    )


async def test_register_cards_runs_again_even_after_the_frontend_is_registered(
    hass: HomeAssistant,
) -> None:
    """`async_register_cards` has no sentinel guard - it reruns on every setup."""
    hass.data.setdefault(DOMAIN, {})[FRONTEND_REGISTERED] = True
    resources = FakeResources()
    hass.data["lovelace"] = FakeLovelace(resources)

    with patch.object(frontend_module, "_get_version", wraps=_get_version) as get_version:
        await async_register_cards(hass)
        first_pass = list(resources.created)
        resources._items = list(
            {"id": f"id-{i}", **item} for i, item in enumerate(resources.created)
        )
        await async_register_cards(hass)

    assert len(first_pass) == len(CARDS), (
        "the frontend_registered sentinel must not suppress card registration"
    )
    assert get_version.call_count == 2, "manifest.json is re-read on every call, with no caching"
    assert resources.created == first_pass, (
        "the second pass should find every resource already current and create nothing"
    )
    assert resources.updated == [], (
        "a second pass over already-current resources should update nothing"
    )


async def test_real_setup_registers_all_five_cards_with_lovelace(
    hass: HomeAssistant, init_integration: ConfigEntry
) -> None:
    """The unbypassed production path against Home Assistant's real resource store."""
    resources = hass.data["lovelace"].resources
    assert resources is not None, "the Lovelace resource collection should be loaded"

    registered = [item for item in resources.async_items() if item["url"].startswith(URL_BASE)]
    assert [item["url"] for item in registered] == [_versioned(card) for card in CARDS], (
        "all eight cards should be present, versioned, and in CARDS order"
    )
    assert {item["type"] for item in registered} == {"module"}, (
        "cards are ES modules; Lovelace stores that under the 'type' key"
    )


async def test_real_setup_does_not_duplicate_resources_on_a_reload(
    hass: HomeAssistant, init_integration: ConfigEntry
) -> None:
    """Reloading the entry re-runs card registration but must stay idempotent."""
    resources = hass.data["lovelace"].resources
    before = [item["url"] for item in resources.async_items()]

    assert await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    after = [item["url"] for item in resources.async_items()]
    assert after == before, (
        "a reload should leave the Lovelace resource list byte-identical; "
        f"before={before} after={after}"
    )


def test_manifest_version_matches_the_value_these_tests_assert() -> None:
    """Guard rail: bumping the integration version must update this module."""
    manifest = json.loads(
        (frontend_module.Path(frontend_module.__file__).parent / "manifest.json").read_text()
    )
    assert manifest["version"] == MANIFEST_VERSION, (
        "manifest.json version changed; update MANIFEST_VERSION in this test module"
    )
