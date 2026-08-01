"""Lifecycle tests for `custom_components.choremander.__init__`.

Covers `async_setup_entry`, `async_unload_entry`, `_get_coordinator` and the
service registration / unregistration sentinels.

Several assertions below deliberately pin *current* (buggy) behaviour. Those are
marked with a `CURRENT BEHAVIOUR (defect #N)` comment so that a future fix shows
up as a deliberate, test-visible change rather than a silent regression.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander import (
    PLATFORMS,
    SERVICES_REGISTERED,
    _get_coordinator,
    async_unload_entry,
)
from custom_components.choremander import const as choremander_const
from custom_components.choremander.const import DOMAIN
from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.frontend import FRONTEND_REGISTERED

# Every service `_async_register_services` registers, in registration order.
ALL_SERVICES = [
    "complete_chore",
    "approve_chore",
    "reject_chore",
    "claim_reward",
    "approve_reward",
    "add_points",
    "remove_points",
    "set_chore_order",
]

DEFAULT_STORE_DATA = {
    "children": [],
    "chores": [],
    "rewards": [],
    "completions": [],
    "reward_claims": [],
    "points_name": "Stars",
    "points_icon": "mdi:star",
}


def _store_payload(entry_id: str, **overrides: Any) -> dict[str, Any]:
    """Build a `hass_storage` payload for one Choremander entry."""
    data = dict(DEFAULT_STORE_DATA) | overrides
    key = f"choremander.storage.{entry_id}"
    return {"version": 1, "minor_version": 1, "key": key, "data": data}


def _make_entry(entry_id: str, data: dict[str, Any] | None = None) -> MockConfigEntry:
    """A Choremander config entry that does not collide with `mock_config_entry`."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Choremander",
        data={"points_name": "Stars", "points_icon": "mdi:star"} if data is None else data,
        entry_id=entry_id,
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id), (
        f"entry {entry.entry_id} failed to set up"
    )
    await hass.async_block_till_done()


# ---------------------------------------------------------------------------
# Frontend registration vs. service teardown
# ---------------------------------------------------------------------------


async def test_real_frontend_path_leaves_services_registered_after_last_unload(
    hass: HomeAssistant, init_integration: ConfigEntry
) -> None:
    """Unloading the last entry does NOT tear services down in production.

    This test deliberately does NOT request `bypass_frontend_registration`, so
    `frontend.py` writes its `frontend_registered` sentinel into the very dict
    `async_unload_entry` scans for remaining entries.
    """
    assert hass.data[DOMAIN][FRONTEND_REGISTERED] is True, (
        "the real frontend path should have marked itself registered; "
        "if this fails the frontend bypass leaked into this test"
    )

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    # CURRENT BEHAVIOUR (defect #1, see test plan 6.2) - `frontend_registered`
    # is counted as a "remaining entry", so the last unload never reaches
    # `_async_unregister_services` and the `services_registered` sentinel stays
    # True. A reload therefore leaves 8 orphaned services behind.
    assert hass.data[DOMAIN] == {FRONTEND_REGISTERED: True, SERVICES_REGISTERED: True}, (
        "expected the frontend sentinel to masquerade as a remaining entry, "
        f"got {hass.data[DOMAIN]!r}"
    )
    still_registered = [s for s in ALL_SERVICES if hass.services.has_service(DOMAIN, s)]
    assert still_registered == ALL_SERVICES, (
        "all 8 services should still be registered after the last unload "
        f"(defect #1); missing: {sorted(set(ALL_SERVICES) - set(still_registered))}"
    )


async def test_bypassing_frontend_registration_lets_the_last_unload_remove_services(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    init_integration: ConfigEntry,
) -> None:
    """With the frontend stubbed out, teardown behaves as the author intended.

    Documents that `bypass_frontend_registration` is not behaviour-neutral: it
    is the difference between defect #1 firing and not firing.
    """
    assert FRONTEND_REGISTERED not in hass.data[DOMAIN], (
        "the bypass fixture should have prevented the frontend sentinel"
    )

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert list(hass.data[DOMAIN]) == [SERVICES_REGISTERED], (
        f"only the services sentinel should remain, got {list(hass.data[DOMAIN])!r}"
    )
    assert hass.data[DOMAIN][SERVICES_REGISTERED] is False, (
        "the services sentinel should be flipped back to False on the last unload"
    )
    leftover = [s for s in ALL_SERVICES if hass.services.has_service(DOMAIN, s)]
    assert leftover == [], f"no service should survive the last unload, found {leftover!r}"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def test_setup_stores_the_coordinator_under_the_entry_id(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    init_integration: ConfigEntry,
) -> None:
    """The coordinator is reachable at `hass.data[DOMAIN][entry_id]`."""
    assert init_integration.state is ConfigEntryState.LOADED
    stored = hass.data[DOMAIN][init_integration.entry_id]
    assert isinstance(stored, ChoremanderCoordinator), (
        f"expected a ChoremanderCoordinator under the entry id, got {type(stored)!r}"
    )
    assert stored.entry_id == init_integration.entry_id


async def test_setup_overwrites_saved_points_settings_from_the_config_entry(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    hass_storage: dict[str, Any],
) -> None:
    """Point settings stored by the options flow are reverted on every restart."""
    hass_storage["choremander.storage.reverted"] = _store_payload(
        "reverted", points_name="Coins", points_icon="mdi:cash"
    )
    entry = _make_entry("reverted")
    await _setup(hass, entry)

    coordinator: ChoremanderCoordinator = hass.data[DOMAIN]["reverted"]
    # CURRENT BEHAVIOUR (defect #7, see test plan 6.2) - `async_setup_entry`
    # unconditionally re-applies `entry.data`, so anything the options flow
    # wrote into storage is silently discarded at the next startup.
    assert coordinator.storage.get_points_name() == "Stars", (
        "entry.data overwrote the stored points name; the options-flow value "
        "'Coins' was discarded on setup"
    )
    assert coordinator.storage.get_points_icon() == "mdi:star", (
        "entry.data overwrote the stored points icon"
    )


async def test_setup_keeps_saved_points_settings_when_the_entry_carries_none(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    hass_storage: dict[str, Any],
) -> None:
    """An entry with no point settings leaves storage alone - but still saves."""
    hass_storage["choremander.storage.empty_data"] = _store_payload(
        "empty_data", points_name="Coins", points_icon="mdi:cash"
    )
    entry = _make_entry("empty_data", data={})
    await _setup(hass, entry)

    coordinator: ChoremanderCoordinator = hass.data[DOMAIN]["empty_data"]
    assert coordinator.storage.get_points_name() == "Coins", (
        "falsy entry.data must not clobber the stored points name"
    )
    assert coordinator.storage.get_points_icon() == "mdi:cash", (
        "falsy entry.data must not clobber the stored points icon"
    )
    # `await coordinator.storage.async_save()` runs unconditionally, so the
    # store is rewritten even though nothing changed.
    assert hass_storage["choremander.storage.empty_data"]["data"]["points_name"] == "Coins", (
        "setup should have re-saved the untouched storage payload"
    )


async def test_setup_forwards_exactly_three_platforms_in_declared_order(
    hass: HomeAssistant, bypass_frontend_registration: None
) -> None:
    """Sensor, button and binary_sensor are forwarded once, after the coordinator lands."""
    forwarded: list[tuple[str, list[Platform]]] = []
    coordinator_present: list[bool] = []

    async def _capture(entry: ConfigEntry, platforms: list[Platform]) -> None:
        coordinator_present.append(
            isinstance(hass.data.get(DOMAIN, {}).get(entry.entry_id), ChoremanderCoordinator)
        )
        forwarded.append((entry.entry_id, list(platforms)))

    entry = _make_entry("forward_entry")
    with patch.object(hass.config_entries, "async_forward_entry_setups", side_effect=_capture):
        await _setup(hass, entry)

    assert forwarded == [
        ("forward_entry", [Platform.SENSOR, Platform.BUTTON, Platform.BINARY_SENSOR])
    ], f"unexpected platform forwarding: {forwarded!r}"
    assert coordinator_present == [True], (
        "the coordinator must already be in hass.data when platforms are "
        "forwarded, otherwise the platform setups cannot find it"
    )


async def test_setting_up_a_second_entry_does_not_reregister_services(
    hass: HomeAssistant, bypass_frontend_registration: None
) -> None:
    """The `services_registered` sentinel makes registration a once-per-hass job."""
    await _setup(hass, _make_entry("entry_one"))
    assert hass.data[DOMAIN][SERVICES_REGISTERED] is True

    register = AsyncMock(return_value=None)
    with patch("custom_components.choremander._async_register_services", register):
        await _setup(hass, _make_entry("entry_two"))

    assert register.call_count == 0, (
        "services were registered a second time; the SERVICES_REGISTERED "
        "sentinel is not being honoured"
    )
    assert all(hass.services.has_service(DOMAIN, s) for s in ALL_SERVICES)


# ---------------------------------------------------------------------------
# Unload
# ---------------------------------------------------------------------------


async def test_unloading_one_of_two_entries_keeps_services_registered(
    hass: HomeAssistant, bypass_frontend_registration: None
) -> None:
    """Services only go away once the last entry is gone."""
    entry_one = _make_entry("entry_one")
    entry_two = _make_entry("entry_two")
    await _setup(hass, entry_one)
    await _setup(hass, entry_two)

    assert await hass.config_entries.async_unload(entry_one.entry_id)
    await hass.async_block_till_done()

    assert "entry_one" not in hass.data[DOMAIN]
    assert "entry_two" in hass.data[DOMAIN], "the surviving entry was torn down too"
    assert hass.data[DOMAIN][SERVICES_REGISTERED] is True, (
        "services must stay registered while another entry is still loaded"
    )
    assert all(hass.services.has_service(DOMAIN, s) for s in ALL_SERVICES), (
        "unloading one of two entries removed the shared services"
    )


async def test_unloading_the_same_entry_twice_raises_keyerror(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    init_integration: ConfigEntry,
) -> None:
    """A second unload blows up on the bare `dict.pop` of the entry id."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()

    # CURRENT BEHAVIOUR (defect #5, see test plan 6.2) - `async_unload_entry`
    # pops without a default, so any second teardown of the same entry raises
    # instead of being a no-op.
    with pytest.raises(KeyError) as excinfo:
        await async_unload_entry(hass, init_integration)

    assert excinfo.value.args[0] == init_integration.entry_id, (
        f"the KeyError should name the already-removed entry id, got {excinfo.value.args[0]!r}"
    )


async def test_unload_returns_false_and_keeps_state_when_platforms_refuse(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    init_integration: ConfigEntry,
) -> None:
    """A failed platform unload aborts teardown without mutating `hass.data`."""
    before = dict(hass.data[DOMAIN])

    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        result = await async_unload_entry(hass, init_integration)

    assert result is False, "async_unload_entry should report the platform failure"
    assert hass.data[DOMAIN] == before, (
        f"hass.data must be untouched when the platform unload fails, got {hass.data[DOMAIN]!r}"
    )
    assert all(hass.services.has_service(DOMAIN, s) for s in ALL_SERVICES), (
        "services must survive a failed unload"
    )


# ---------------------------------------------------------------------------
# _get_coordinator
# ---------------------------------------------------------------------------


async def test_get_coordinator_skips_the_services_sentinel(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    coordinator: ChoremanderCoordinator,
) -> None:
    """Sentinel keys/values are not mistaken for a coordinator."""
    assert _get_coordinator(hass) is coordinator, (
        "the live coordinator should be found past the sentinel keys"
    )


async def test_get_coordinator_returns_none_when_only_sentinels_remain(
    hass: HomeAssistant, bypass_frontend_registration: None
) -> None:
    """No entries loaded means no coordinator, not a sentinel value."""
    hass.data[DOMAIN] = {SERVICES_REGISTERED: True, FRONTEND_REGISTERED: True}
    assert _get_coordinator(hass) is None, "sentinel booleans must not be returned as a coordinator"


async def test_get_coordinator_returns_none_when_domain_absent(
    hass: HomeAssistant, bypass_frontend_registration: None
) -> None:
    """`_get_coordinator` tolerates the integration never having been set up."""
    hass.data.pop(DOMAIN, None)
    assert _get_coordinator(hass) is None


async def test_get_coordinator_returns_the_first_entry_by_insertion_order(
    hass: HomeAssistant, bypass_frontend_registration: None
) -> None:
    """With two entries loaded, every service call targets the first one."""
    entry_one = _make_entry("entry_one")
    entry_two = _make_entry("entry_two")
    await _setup(hass, entry_one)
    await _setup(hass, entry_two)

    first = hass.data[DOMAIN]["entry_one"]
    second = hass.data[DOMAIN]["entry_two"]
    assert first is not second

    assert _get_coordinator(hass) is first, (
        "services silently target whichever entry was set up first; a second "
        "household's entry is unreachable through the service API"
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_const_platforms_is_a_dead_parallel_to_the_platforms_init_actually_uses() -> None:
    """`const.PLATFORMS` is plain strings and is never read by `__init__`."""
    assert PLATFORMS is not choremander_const.PLATFORMS
    assert all(isinstance(platform, Platform) for platform in PLATFORMS), (
        "__init__.PLATFORMS should be Platform enum members"
    )
    assert all(type(name) is str for name in choremander_const.PLATFORMS), (
        "const.PLATFORMS should be a plain list of strings"
    )
    assert [str(p.value) for p in PLATFORMS] == choremander_const.PLATFORMS, (
        "the two lists have drifted apart; const.PLATFORMS is unused so "
        "nothing else would catch this"
    )
