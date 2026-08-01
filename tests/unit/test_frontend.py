"""Tests for Choremander's static-path and Lovelace resource registration.

These exercise the real registration path - the `bypass_frontend_registration`
fixture is deliberately NOT requested here, because bypassing it is exactly what
this module is meant to cover.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.config_entries import ConfigEntry
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
    _get_version,
    async_register_cards,
    async_register_frontend,
)

MANIFEST_VERSION = "1.0.4"


class FakeResources:
    """Stand-in for Lovelace's ResourceStorageCollection.

    Hand-written rather than a MagicMock so that `async_items()` really returns
    dicts - the production code indexes into them and splits their URLs.
    """

    def __init__(self, items: list[dict[str, Any]] | None = None) -> None:
        self._items = items or []
        self.created: list[dict[str, Any]] = []
        self.updated: list[tuple[str, dict[str, Any]]] = []

    def async_items(self) -> list[dict[str, Any]]:
        return list(self._items)

    async def async_create_item(self, data: dict[str, Any]) -> dict[str, Any]:
        self.created.append(data)
        return data

    async def async_update_item(self, item_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        self.updated.append((item_id, updates))
        return updates


class FakeLovelace:
    """Stand-in for LovelaceData."""

    def __init__(self, resources: FakeResources | None = None, mode: str | None = None) -> None:
        self.resources = resources
        if mode is not None:
            self.mode = mode


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
    """All five cards are registered as versioned ES modules, in `CARDS` order."""
    resources = FakeResources()
    hass.data["lovelace"] = FakeLovelace(resources)

    await async_register_cards(hass)

    assert resources.created == [
        {"url": _versioned(card), "res_type": "module"} for card in CARDS
    ], "the five cards should be created in CARDS order with a ?v= cache buster"
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
    hass.data["lovelace"] = FakeLovelace(resources, mode="yaml")

    await async_register_cards(hass)

    assert resources.created == [], "YAML mode must not write to the storage collection"
    assert resources.updated == []
    for card in CARDS:
        assert f"- url: {URL_BASE}/{card}" in caplog.text, (
            f"YAML-mode instructions should list {card} for manual configuration"
        )


async def test_yaml_mode_branch_is_unreachable_because_lovelace_exposes_resource_mode(
    hass: HomeAssistant, init_integration: ConfigEntry
) -> None:
    """CURRENT BEHAVIOUR: the YAML-mode guard reads an attribute that does not exist.

    `frontend.py` does `getattr(lovelace_data, "mode", "storage")`, but Home
    Assistant's `LovelaceData` exposes `resource_mode`, not `mode`. The YAML
    branch above is therefore dead code against a real install - it always
    takes the storage path. Pinned so a future rename is deliberate.
    """
    lovelace_data = hass.data["lovelace"]

    assert not hasattr(lovelace_data, "mode"), (
        "LovelaceData grew a `mode` attribute; the YAML-mode branch in "
        "frontend.py is now live and its behaviour must be re-verified"
    )
    assert hasattr(lovelace_data, "resource_mode"), (
        "LovelaceData should expose resource_mode - that is the attribute "
        "frontend.py ought to be reading"
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
        "all five cards should be present, versioned, and in CARDS order"
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
