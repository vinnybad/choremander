"""Tests for recovering Lovelace resources that older versions deleted.

The repair pairs three independent facts back together: a dashboard asks for
`custom:<element>`, a file under `config/www` defines that element, and no
registered resource points at that file. These tests drive each of those
signals separately, because the whole value of the feature is that it only
fires when all three agree.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.components.lovelace.const import ConfigNotFound
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander import RESOURCE_CHECK_SCHEDULED
from custom_components.choremander.const import DOMAIN
from custom_components.choremander.repairs import (
    RestoreResourcesRepairFlow,
    async_create_fix_flow,
)
from custom_components.choremander.resource_repair import (
    ISSUE_ID,
    _best_candidate,
    _iter_custom_types,
    _relative_path_for_url,
    _scan_www,
    _url_for_relative_path,
    async_check_for_deleted_resources,
    async_find_restorable_resources,
    async_restore_resources,
)
from tests.unit.test_frontend import FakeLovelace, FakeResources


def _card_source(*elements: str) -> str:
    body = "\n".join(
        f'customElements.define("{element}", class extends HTMLElement {{}});'
        for element in elements
    )
    return f"// a custom card\n{body}\n"


class FakeDashboard:
    """Stand-in for LovelaceStorage / LovelaceYAML."""

    def __init__(self, config: Any, *, error: Exception | None = None) -> None:
        self._config = config
        self._error = error

    async def async_load(self, force: bool) -> Any:
        if self._error is not None:
            raise self._error
        return self._config


@pytest.fixture
def www(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Point `hass.config.path("www")` at a writable throwaway directory.

    The real test config directory lives inside site-packages and must not be
    written to.
    """
    hass.config.config_dir = str(tmp_path)
    root = tmp_path / "www"
    root.mkdir()
    return root


def _dashboard_using(*card_types: str) -> FakeDashboard:
    return FakeDashboard(
        {"views": [{"cards": [{"type": f"custom:{card_type}"} for card_type in card_types]}]}
    )


def _setup_lovelace(
    hass: HomeAssistant,
    *,
    resources: FakeResources,
    dashboards: dict[str | None, FakeDashboard],
) -> FakeLovelace:
    lovelace = FakeLovelace(resources)
    lovelace.dashboards = dashboards
    hass.data["lovelace"] = lovelace
    return lovelace


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def test_custom_types_are_found_however_deeply_they_are_nested() -> None:
    """Cards nest inside stacks, grids and conditionals; all of them count."""
    config = {
        "views": [
            {
                "cards": [
                    {"type": "custom:top-level-card"},
                    {
                        "type": "vertical-stack",
                        "cards": [
                            {"type": "custom:nested-card"},
                            {"type": "entities", "entities": ["sensor.x"]},
                        ],
                    },
                ],
                "badges": [{"type": "custom:badge-card"}],
            }
        ]
    }

    assert _iter_custom_types(config) == {
        "top-level-card",
        "nested-card",
        "badge-card",
    }, "every custom: type in the tree should be collected, built-ins ignored"


def test_built_in_cards_are_not_mistaken_for_custom_ones() -> None:
    assert _iter_custom_types({"type": "entities"}) == set()
    assert _iter_custom_types({"type": None}) == set()
    assert _iter_custom_types([]) == set()


def test_scan_www_maps_each_defined_element_to_its_file(www: Path) -> None:
    """Both quote styles and multiple definitions per file are picked up."""
    (www / "community").mkdir()
    (www / "community" / "button-card").mkdir()
    (www / "community" / "button-card" / "button-card.js").write_text(
        'customElements.define("button-card", class {});\n'
        "customElements.define('button-card-editor', class {});\n"
    )
    (www / "loose.js").write_text(_card_source("loose-card"))
    (www / "notes.txt").write_text("customElements.define('not-js', 0)")

    found = _scan_www(www)

    assert found == {
        "button-card": ["community/button-card/button-card.js"],
        "button-card-editor": ["community/button-card/button-card.js"],
        "loose-card": ["loose.js"],
    }, "only .js files should be scanned, and paths should be www-relative"


def test_scan_www_records_every_file_that_defines_the_same_element(www: Path) -> None:
    """A repo often ships a built bundle next to the sources it came from."""
    (www / "community").mkdir()
    (www / "community" / "fancy").mkdir()
    (www / "community" / "fancy" / "fancy-card.js").write_text(_card_source("fancy-card"))
    (www / "community" / "fancy" / "src.js").write_text(_card_source("fancy-card"))

    assert _scan_www(www)["fancy-card"] == [
        "community/fancy/fancy-card.js",
        "community/fancy/src.js",
    ], "both definitions should be recorded so the caller can tell they are the same card"


async def test_a_card_is_reachable_when_any_file_defining_it_is_registered(
    hass: HomeAssistant, www: Path
) -> None:
    """Registering the bundle must not leave the loose source looking orphaned.

    Otherwise Choremander would offer to register a file that is not a loadable
    module, on top of the bundle that already works.
    """
    (www / "community").mkdir()
    (www / "community" / "fancy").mkdir()
    (www / "community" / "fancy" / "fancy-card.js").write_text(_card_source("fancy-card"))
    (www / "community" / "fancy" / "aaa-source.js").write_text(_card_source("fancy-card"))

    resources = FakeResources([{"id": "bundle", "url": "/hacsfiles/fancy/fancy-card.js"}])
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("fancy-card")})

    assert await async_find_restorable_resources(hass, resources) == [], (
        "the card already loads from the registered bundle; the source file "
        "sorting first must not be offered as a missing resource"
    )


def test_scan_www_tolerates_a_missing_directory(tmp_path: Path) -> None:
    assert _scan_www(tmp_path / "nope") == {}


def test_scan_www_skips_files_that_are_too_large(www: Path, monkeypatch) -> None:
    """A multi-megabyte blob is not a card and should not be read into memory."""
    monkeypatch.setattr("custom_components.choremander.resource_repair._MAX_FILE_BYTES", 10)
    (www / "huge.js").write_text(_card_source("huge-card"))

    assert _scan_www(www) == {}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/hacsfiles/button-card/button-card.js", "community/button-card/button-card.js"),
        ("/local/loose.js", "loose.js"),
        ("/local/community/x/y.js", "community/x/y.js"),
        ("/local/loose.js?v=1.2.3", "loose.js"),
        ("/choremander/choremander-child-card.js", None),
        ("https://cdn.example.com/card.js", None),
    ],
)
def test_resource_urls_map_back_onto_www_paths(url: str, expected: str | None) -> None:
    """Only URLs Home Assistant serves out of config/www can be reasoned about."""
    assert _relative_path_for_url(url) == expected


def test_hacs_files_are_restored_under_the_url_hacs_itself_would_use() -> None:
    """Matching HACS's URL means a later reinstall updates the row, not duplicates it."""
    relative = "community/button-card/button-card.js"

    assert _url_for_relative_path(relative, hacs_loaded=True) == (
        "/hacsfiles/button-card/button-card.js"
    )
    assert _url_for_relative_path(relative, hacs_loaded=False) == (
        "/local/community/button-card/button-card.js"
    )
    assert _url_for_relative_path("loose.js", hacs_loaded=True) == "/local/loose.js"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


async def test_a_wanted_card_present_on_disk_but_unregistered_is_restorable(
    hass: HomeAssistant, www: Path
) -> None:
    """The exact shape of the damage: dashboard needs it, file is there, no row."""
    (www / "mini-graph-card.js").write_text(_card_source("mini-graph-card"))
    resources = FakeResources()
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("mini-graph-card")},
    )

    (restorable,) = await async_find_restorable_resources(hass, resources)

    assert restorable.url == "/local/mini-graph-card.js"
    assert restorable.relative_path == "mini-graph-card.js"
    assert restorable.card_types == ("mini-graph-card",)


async def test_a_card_that_is_still_registered_is_left_alone(
    hass: HomeAssistant, www: Path
) -> None:
    """Nothing is restored when the resource is already reachable."""
    (www / "mini-graph-card.js").write_text(_card_source("mini-graph-card"))
    resources = FakeResources([{"id": "a", "url": "/local/mini-graph-card.js?v=2"}])
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("mini-graph-card")},
    )

    assert await async_find_restorable_resources(hass, resources) == [], (
        "a registered resource must be recognised even with a cache-busting query"
    )


async def test_a_card_no_dashboard_uses_is_not_restored(hass: HomeAssistant, www: Path) -> None:
    """Leftover files are not evidence of deletion - the user may have moved on."""
    (www / "unused-card.js").write_text(_card_source("unused-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using()})

    assert await async_find_restorable_resources(hass, resources) == []


async def test_a_wanted_card_that_is_not_on_disk_is_not_invented(
    hass: HomeAssistant, www: Path
) -> None:
    """We can only restore a row when we can point it at a real file."""
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("ghost-card")})

    assert await async_find_restorable_resources(hass, resources) == []


async def test_one_file_defining_several_wanted_cards_yields_one_resource(
    hass: HomeAssistant, www: Path
) -> None:
    """A bundle is registered once, not once per element it defines."""
    (www / "bundle.js").write_text(_card_source("alpha-card", "beta-card"))
    resources = FakeResources()
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("alpha-card", "beta-card")},
    )

    (restorable,) = await async_find_restorable_resources(hass, resources)

    assert restorable.url == "/local/bundle.js"
    assert restorable.card_types == ("alpha-card", "beta-card")


async def test_a_dashboard_that_cannot_be_loaded_is_skipped(hass: HomeAssistant, www: Path) -> None:
    """Auto-generated and strategy dashboards raise rather than return a config."""
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={
            # The real exception Home Assistant raises for a dashboard with no
            # stored config; it subclasses HomeAssistantError.
            None: FakeDashboard(None, error=ConfigNotFound),
            "extra": _dashboard_using("wanted-card"),
        },
    )

    (restorable,) = await async_find_restorable_resources(hass, resources)

    assert restorable.url == "/local/card.js", (
        "one unreadable dashboard must not stop the others being examined"
    )


async def test_a_card_served_from_a_custom_static_path_is_not_flagged(
    hass: HomeAssistant, www: Path
) -> None:
    """Choremander's own cards are the case that caught this.

    They are registered at `/choremander/<card>.js`, which is a static path of
    the integration's own and maps onto no `config/www` path at all. In the dev
    container the very same files are also bind-mounted into
    `config/www/choremander/`, so matching purely on www-relative paths reported
    every Choremander card as deleted and offered to add a second copy of each.
    """
    (www / "choremander").mkdir()
    (www / "choremander" / "choremander-child-card.js").write_text(
        _card_source("choremander-child-card")
    )
    resources = FakeResources(
        [{"id": "own", "url": "/choremander/choremander-child-card.js?v=1.1.0"}]
    )
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("choremander-child-card")},
    )

    assert await async_find_restorable_resources(hass, resources) == [], (
        "a card already served from a non-www static path must not be restored"
    )


async def test_the_bundle_is_restored_rather_than_the_source_beside_it(
    hass: HomeAssistant, www: Path
) -> None:
    """The case this whole feature exists for, with more than one candidate.

    A card repo ships a built bundle next to the sources it came from, both
    calling `customElements.define("fancy-card")`. When the row was deleted,
    neither is registered, so there is no registration to disambiguate them.
    Picking alphabetically would register `aaa-source.js` - a resource that
    silently fails to load - and then close the repair, leaving the user worse
    off than before with no way to retry.
    """
    (www / "community").mkdir()
    (www / "community" / "fancy").mkdir()
    (www / "community" / "fancy" / "fancy-card.js").write_text(_card_source("fancy-card"))
    (www / "community" / "fancy" / "aaa-source.js").write_text(_card_source("fancy-card"))
    (www / "community" / "fancy" / "src").mkdir()
    (www / "community" / "fancy" / "src" / "fancy-card.js").write_text(_card_source("fancy-card"))

    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("fancy-card")})

    (restorable,) = await async_find_restorable_resources(hass, resources)

    assert restorable.relative_path == "community/fancy/fancy-card.js", (
        "the file named after the element, at the shallowest depth, is the "
        f"loadable bundle; got {restorable.relative_path}"
    )


@pytest.mark.parametrize(
    ("card_type", "candidates", "expected"),
    [
        # An exact filename match beats anything else.
        ("my-card", ["a.js", "my-card.js"], "my-card.js"),
        # Then a name containing the element name.
        ("my-card", ["a.js", "my-card-bundle.js"], "my-card-bundle.js"),
        # Then the shallowest path.
        ("my-card", ["src/my-card.js", "my-card.js"], "my-card.js"),
        # Then the shortest, then alphabetical, so the result is deterministic.
        ("my-card", ["zz.js", "a.js"], "a.js"),
    ],
)
def test_the_candidate_ranking_prefers_the_likely_bundle(
    card_type: str, candidates: list[str], expected: str
) -> None:
    assert _best_candidate(card_type, candidates) == expected


async def test_a_wanted_card_missing_from_disk_is_skipped_while_others_restore(
    hass: HomeAssistant, www: Path
) -> None:
    """One un-findable card must not stop the findable ones being offered."""
    (www / "present.js").write_text(_card_source("present-card"))
    resources = FakeResources()
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("present-card", "absent-card")},
    )

    (restorable,) = await async_find_restorable_resources(hass, resources)

    assert restorable.card_types == ("present-card",), (
        "only the card whose file was found should be offered; absent-card has "
        "no file to point a resource at"
    )


async def test_a_www_resource_does_not_vouch_for_a_different_file_of_the_same_name(
    hass: HomeAssistant, www: Path
) -> None:
    """Bare-name matching is only a fallback for URLs outside `config/www`.

    A registered `/hacsfiles/foo/index.js` resolves to a www path of its own, so
    it says nothing about an unrelated `community/bar/index.js` that was deleted.
    Folding its filename into the match would hide a genuine casualty.
    """
    for repo in ("foo", "bar"):
        (www / "community" / repo).mkdir(parents=True)
        (www / "community" / repo / "index.js").write_text(_card_source(f"{repo}-card"))

    resources = FakeResources([{"id": "foo", "url": "/hacsfiles/foo/index.js"}])
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("foo-card", "bar-card")},
    )

    (restorable,) = await async_find_restorable_resources(hass, resources)

    assert restorable.relative_path == "community/bar/index.js", (
        "bar's deleted resource shares a filename with foo's registered one, "
        "but they are different files and bar is genuinely missing"
    )


async def test_a_module_loaded_via_extra_js_url_counts_as_reachable(
    hass: HomeAssistant, www: Path
) -> None:
    """Choremander loads its own sound module that way; it is not missing."""
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "frontend", {})
    (www / "global.js").write_text(_card_source("global-card"))
    add_extra_js_url(hass, "/local/global.js?v=1")

    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("global-card")})

    assert await async_find_restorable_resources(hass, resources) == []


# ---------------------------------------------------------------------------
# The repair issue
# ---------------------------------------------------------------------------


async def test_the_repair_issue_names_every_resource_it_would_restore(
    hass: HomeAssistant, www: Path
) -> None:
    """The user should see the exact list before agreeing to anything."""
    (www / "one.js").write_text(_card_source("one-card"))
    (www / "two.js").write_text(_card_source("two-card"))
    resources = FakeResources()
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("one-card", "two-card")},
    )

    await async_check_for_deleted_resources(hass)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None, "missing resources should raise a repair issue"
    assert issue.is_fixable, "the issue must offer the one-click restore"
    assert issue.translation_placeholders["count"] == "2"
    assert "/local/one.js" in issue.translation_placeholders["resources"]
    assert "/local/two.js" in issue.translation_placeholders["resources"]


async def test_no_issue_is_raised_when_nothing_is_missing(hass: HomeAssistant, www: Path) -> None:
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using()})

    await async_check_for_deleted_resources(hass)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_the_issue_clears_once_the_user_re_adds_the_resource_by_hand(
    hass: HomeAssistant, www: Path
) -> None:
    """Someone who fixed it themselves should not keep being nagged."""
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})

    await async_check_for_deleted_resources(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    resources._items.append({"id": "manual", "url": "/local/card.js"})
    await async_check_for_deleted_resources(hass)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_the_check_loads_the_collection_before_reading_it(
    hass: HomeAssistant, www: Path
) -> None:
    """An unloaded collection reports no resources, which would look like damage."""
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources([{"id": "a", "url": "/local/card.js"}], loaded=False)
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})

    await async_check_for_deleted_resources(hass)

    assert resources.load_calls == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None, (
        "the resource is registered; reading an unloaded collection would have "
        "reported it missing and offered to add a duplicate"
    )


async def test_the_check_never_raises_when_lovelace_is_unavailable(
    hass: HomeAssistant,
) -> None:
    hass.data.pop("lovelace", None)

    await async_check_for_deleted_resources(hass)  # must not raise

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_the_check_is_a_noop_when_the_resource_collection_is_none(
    hass: HomeAssistant,
) -> None:
    """`LovelaceData.resources` is None before the collection is built."""
    lovelace = FakeLovelace(resources=None)
    lovelace.dashboards = {}
    hass.data["lovelace"] = lovelace

    await async_check_for_deleted_resources(hass)  # must not raise

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None


async def test_a_failing_scan_warns_and_raises_no_issue(
    hass: HomeAssistant, www: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A read-only scan must not break startup, but must not fail silently either."""
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})

    with patch(
        "custom_components.choremander.resource_repair._scan_www",
        side_effect=PermissionError("www is not readable"),
    ):
        await async_check_for_deleted_resources(hass)  # must not raise

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None, (
        "a failed scan proves nothing, so it must not raise a repair"
    )
    assert "Could not check for deleted Lovelace resources" in caplog.text, (
        "a user whose scan fails needs to see why they were offered no repair"
    )
    assert "www is not readable" in caplog.text, (
        "the underlying error should be logged, not swallowed"
    )


# ---------------------------------------------------------------------------
# Restoring
# ---------------------------------------------------------------------------


async def test_restoring_registers_every_missing_resource_as_a_module(
    hass: HomeAssistant, www: Path
) -> None:
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})
    await async_check_for_deleted_resources(hass)

    restored = await async_restore_resources(hass)

    assert restored == ["/local/card.js"]
    assert resources.created == [{"url": "/local/card.js", "res_type": "module"}]
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None, (
        "the issue should close once the restore has run"
    )


async def test_restoring_writes_nothing_when_there_is_nothing_to_restore(
    hass: HomeAssistant, www: Path
) -> None:
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using()})

    assert await async_restore_resources(hass) == []
    assert resources.created == []


async def test_restoring_refuses_to_write_to_a_collection_it_cannot_verify(
    hass: HomeAssistant, www: Path
) -> None:
    """The rule that caused issue #2 applies to the repair itself."""
    from tests.unit.test_frontend import LoadStatelessResources

    resources = LoadStatelessResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})

    with pytest.raises(HomeAssistantError, match="not available to write"):
        await async_restore_resources(hass)

    assert resources.created == []


async def test_restoring_loads_a_lazy_collection_first(hass: HomeAssistant, www: Path) -> None:
    """Otherwise the restore would truncate the store exactly as issue #2 did."""
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources([{"id": "other", "url": "/local/other.js"}], loaded=False)
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})

    await async_restore_resources(hass)

    assert resources.load_calls == 1
    assert [item["url"] for item in resources.async_items()] == ["/local/other.js"], (
        "the pre-existing row must still be in the collection after the restore"
    )


# ---------------------------------------------------------------------------
# The fix flow
# ---------------------------------------------------------------------------


async def test_the_fix_flow_restores_only_after_the_user_confirms(
    hass: HomeAssistant, www: Path
) -> None:
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})
    await async_check_for_deleted_resources(hass)

    flow = RestoreResourcesRepairFlow()
    flow.hass = hass

    form = await flow.async_step_init()
    assert form["type"] == "form"
    assert form["step_id"] == "confirm"
    assert form["description_placeholders"]["count"] == "1"
    assert "/local/card.js" in form["description_placeholders"]["resources"]
    assert resources.created == [], "showing the form must not write anything"

    result = await flow.async_step_confirm({})

    assert result["type"] == "create_entry"
    assert resources.created == [{"url": "/local/card.js", "res_type": "module"}]


async def test_the_fix_flow_recomputes_rather_than_trusting_the_stale_issue(
    hass: HomeAssistant, www: Path
) -> None:
    """Between raising the issue and clicking Fix, the user may have re-added some."""
    (www / "one.js").write_text(_card_source("one-card"))
    (www / "two.js").write_text(_card_source("two-card"))
    resources = FakeResources()
    _setup_lovelace(
        hass,
        resources=resources,
        dashboards={None: _dashboard_using("one-card", "two-card")},
    )
    await async_check_for_deleted_resources(hass)

    resources._items.append({"id": "manual", "url": "/local/one.js"})

    flow = RestoreResourcesRepairFlow()
    flow.hass = hass
    form = await flow.async_step_init()

    assert form["description_placeholders"]["count"] == "1", (
        "the form should reflect what is missing now, not when the issue was raised"
    )
    assert "/local/two.js" in form["description_placeholders"]["resources"]
    assert "/local/one.js" not in form["description_placeholders"]["resources"]

    await flow.async_step_confirm({})
    assert resources.created == [{"url": "/local/two.js", "res_type": "module"}], (
        "only the resource that is still missing should be added"
    )


async def test_the_fix_flow_is_only_created_for_a_known_issue(
    hass: HomeAssistant,
) -> None:
    flow = await async_create_fix_flow(hass, ISSUE_ID, None)
    assert isinstance(flow, RestoreResourcesRepairFlow)

    with pytest.raises(ValueError, match="unknown repair"):
        await async_create_fix_flow(hass, "something_else", None)


async def test_the_fix_flow_aborts_when_there_is_nothing_left_to_restore(
    hass: HomeAssistant, www: Path
) -> None:
    """A stale issue should close itself rather than offer an empty list."""
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})
    await async_check_for_deleted_resources(hass)
    resources._items.append({"id": "manual", "url": "/local/card.js"})

    flow = RestoreResourcesRepairFlow()
    flow.hass = hass
    result = await flow.async_step_init()

    assert result["type"] == "abort"
    assert result["reason"] == "nothing_to_restore"
    assert resources.created == []
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None, (
        "the stale issue should be cleared rather than left for the user to dismiss"
    )


# ---------------------------------------------------------------------------
# YAML resource mode
# ---------------------------------------------------------------------------


async def test_yaml_resource_mode_raises_no_repair(hass: HomeAssistant, www: Path) -> None:
    """`ResourceYAMLCollection` is a read-only view of configuration.yaml.

    Its `loaded` is a True class attribute, so the load guard alone lets it
    through - but it has no `async_create_item`, so an offer to restore would
    fail the moment the user pressed Submit.
    """
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    lovelace = _setup_lovelace(
        hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")}
    )
    lovelace.resource_mode = "yaml"

    await async_check_for_deleted_resources(hass)

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None, (
        "resources cannot be written in YAML mode, so no repair should be offered"
    )


async def test_restoring_in_yaml_mode_fails_loudly_instead_of_writing(
    hass: HomeAssistant, www: Path
) -> None:
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    lovelace = _setup_lovelace(
        hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")}
    )
    lovelace.resource_mode = "yaml"

    with pytest.raises(HomeAssistantError, match="YAML mode"):
        await async_restore_resources(hass)

    assert resources.created == []


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


async def test_the_check_is_scheduled_once_and_only_after_startup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Scanning every dashboard and all of www must stay off the setup path."""
    second_entry = MockConfigEntry(domain=DOMAIN, title="Second", entry_id="second")

    with patch("custom_components.choremander.async_at_started") as at_started:
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        second_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(second_entry.entry_id)
        await hass.async_block_till_done()

    assert at_started.call_count == 1, (
        "the check should be scheduled once for the install, not once per entry"
    )
    assert at_started.call_args[0][1] is async_check_for_deleted_resources, (
        "the scheduled callback should be the check itself, with no wrapper"
    )


async def test_removing_the_integration_clears_the_repair(
    hass: HomeAssistant, www: Path, init_integration: ConfigEntry
) -> None:
    """A dangling repair after removal is actively harmful.

    With no Choremander entry there is no repairs platform, so Home Assistant
    falls back to a plain confirm flow: the user's "Fix" click dismisses the
    issue and restores nothing.
    """
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})
    await async_check_for_deleted_resources(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    assert await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is None, (
        "the repair must not outlive the integration that offers to fix it"
    )


async def test_removing_one_of_two_entries_leaves_the_repair_alone(
    hass: HomeAssistant, www: Path, init_integration: ConfigEntry
) -> None:
    """While any entry survives, the repairs platform is registered and works."""
    second = MockConfigEntry(domain=DOMAIN, title="Second", entry_id="second")
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})
    await async_check_for_deleted_resources(hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None

    assert await hass.config_entries.async_remove(init_integration.entry_id)
    await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID) is not None, (
        "one entry is still set up, so the repair is still fixable and must stay"
    )


async def test_unloading_clears_the_sentinel_but_keeps_the_repair(
    hass: HomeAssistant, www: Path, init_integration: ConfigEntry
) -> None:
    """A reload is an unload plus a setup, and must not resurrect a dismissal.

    Deleting the issue would drop the registry entry holding `dismissed_version`
    with it, so the check that runs immediately afterwards would re-raise a
    repair the user had already dismissed - on every options change.
    """
    (www / "card.js").write_text(_card_source("wanted-card"))
    resources = FakeResources()
    _setup_lovelace(hass, resources=resources, dashboards={None: _dashboard_using("wanted-card")})
    await async_check_for_deleted_resources(hass)

    issue = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert issue is not None
    ir.async_ignore_issue(hass, DOMAIN, ISSUE_ID, True)
    dismissed = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID).dismissed_version
    assert dismissed is not None, "precondition: the issue is dismissed"

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert RESOURCE_CHECK_SCHEDULED not in hass.data, (
        "clearing the flag lets a reload schedule the check again"
    )
    surviving = ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_ID)
    assert surviving is not None and surviving.dismissed_version == dismissed, (
        "unloading must preserve the dismissal; a reload would otherwise bring "
        "the repair straight back"
    )


async def test_unloading_cancels_a_check_that_has_not_run_yet(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """An entry on its way out must not leave a scan queued behind it."""
    unsub = MagicMock()
    with patch("custom_components.choremander.async_at_started", return_value=unsub) as at_started:
        mock_config_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert at_started.call_count == 1
    unsub.assert_not_called()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    unsub.assert_called_once_with()


async def test_the_scheduling_sentinel_stays_out_of_the_domain_data(
    hass: HomeAssistant, init_integration: ConfigEntry
) -> None:
    """`hass.data[DOMAIN]` is scanned entry-by-entry when the last entry unloads.

    Every non-entry key left in there makes the final unload think entries
    remain, so services are never unregistered - defect #1 in test_init.py. The
    scheduling flag therefore lives at the top level of `hass.data` instead.
    """
    assert RESOURCE_CHECK_SCHEDULED in hass.data, "the flag should have been set"
    assert RESOURCE_CHECK_SCHEDULED not in hass.data[DOMAIN], (
        "putting the flag in hass.data[DOMAIN] would silently break service "
        "unregistration on the last unload"
    )
    assert RESOURCE_CHECK_SCHEDULED.startswith(DOMAIN), (
        "a top-level hass.data key should be namespaced to the integration"
    )
