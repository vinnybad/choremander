"""Tests for the Choremander binary sensor platform.

`binary_sensor.has_pending_approvals` is the integration's single at-a-glance
"is a parent needed?" signal. Automations key off its state and its three
counter attributes, so the attribute names are pinned exactly.
"""

from __future__ import annotations

import copy
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander.binary_sensor import HasPendingApprovalsBinarySensor
from custom_components.choremander.const import DOMAIN
from custom_components.choremander.coordinator import ChoremanderCoordinator

ENTRY_ID = "seed_entry"
STORE_KEY = f"choremander.storage.{ENTRY_ID}"
BINARY_SENSOR = "binary_sensor.has_pending_approvals"

# One child, one auto-approved chore, one approval-gated chore, one reward.
SEED: dict[str, Any] = {
    "children": [
        {
            "name": "Krishna",
            "avatar": "mdi:account-circle",
            "points": 25,
            "total_points_earned": 0,
            "total_chores_completed": 0,
            "current_streak": 0,
            "best_streak": 0,
            "pending_rewards": [],
            "chore_order": [],
            "id": "child_krishna",
        }
    ],
    "chores": [
        {
            "id": "chore_bed",
            "name": "Make Bed",
            "points": 2,
            "time_category": "morning",
            "assigned_to": [],
            "requires_approval": False,
            "daily_limit": 1,
        },
        {
            "id": "chore_homework",
            "name": "Do Homework",
            "points": 5,
            "time_category": "evening",
            "assigned_to": ["child_krishna"],
            "requires_approval": True,
            "daily_limit": 1,
        },
    ],
    "rewards": [
        {
            "id": "reward_game",
            "name": "New Video Game",
            "cost": 20,
            "icon": "mdi:gamepad-variant",
            "assigned_to": ["child_krishna"],
            "is_jackpot": False,
            "override_point_value": True,
        }
    ],
    "completions": [],
    "reward_claims": [],
    "points_name": "Stars",
    "points_icon": "mdi:star",
}

PENDING_COMPLETION = {
    "chore_id": "chore_homework",
    "child_id": "child_krishna",
    "completed_at": "2026-08-01T19:42:43.589634Z",
    "approved": False,
    "approved_at": None,
    "points_awarded": 0,
    "id": "comp_pending",
}
PENDING_CLAIM = {
    "reward_id": "reward_game",
    "child_id": "child_krishna",
    "claimed_at": "2026-08-01T20:00:00Z",
    "approved": False,
    "approved_at": None,
    "id": "claim_pending",
}
APPROVED_CLAIM = {
    "reward_id": "reward_game",
    "child_id": "child_krishna",
    "claimed_at": "2026-08-01T18:00:00Z",
    "approved": True,
    "approved_at": "2026-08-01T18:05:00Z",
    "id": "claim_done",
}


@pytest.fixture
def seed_data() -> dict[str, Any]:
    """A private, mutable copy of the seed for a single test."""
    return copy.deepcopy(SEED)


@pytest.fixture
def setup_choremander(hass: HomeAssistant, hass_storage: dict, bypass_frontend_registration):
    """Return a coroutine that seeds storage and sets up a config entry."""

    async def _setup(data: dict[str, Any]) -> ConfigEntry:
        hass_storage[STORE_KEY] = {
            "version": 1,
            "minor_version": 1,
            "key": STORE_KEY,
            "data": data,
        }
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Choremander",
            data={"points_name": "Stars", "points_icon": "mdi:star"},
            entry_id=ENTRY_ID,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup


def _payload(state) -> dict[str, Any]:
    """The attributes an automation would read, minus HA's own decoration."""
    return {k: v for k, v in state.attributes.items() if k != "friendly_name"}


async def test_nothing_pending_reports_off_with_the_calm_icon_and_zero_counters(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """An empty queue is the quiet state: off, bell-check, all counters zero."""
    await setup_choremander(seed_data)

    state = hass.states.get(BINARY_SENSOR)
    assert state is not None, f"{BINARY_SENSOR} was never created by the platform"
    assert state.state == "off", "with nothing awaiting approval no parent action is needed"
    assert _payload(state) == {
        "pending_chore_completions": 0,
        "pending_reward_claims": 0,
        "total_pending": 0,
        "device_class": "problem",
        "icon": "mdi:bell-check",
    }, "the off-state payload is a contract for automations and the approvals card"


async def test_an_unapproved_chore_completion_turns_the_sensor_on(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """A chore awaiting a parent flips the sensor and swaps in the alert icon."""
    seed_data["completions"] = [copy.deepcopy(PENDING_COMPLETION)]
    await setup_choremander(seed_data)

    state = hass.states.get(BINARY_SENSOR)
    assert state.state == "on", "one chore is waiting for approval"
    assert _payload(state) == {
        "pending_chore_completions": 1,
        "pending_reward_claims": 0,
        "total_pending": 1,
        "device_class": "problem",
        "icon": "mdi:bell-alert",
    }


async def test_an_unapproved_reward_claim_alone_turns_the_sensor_on(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """Reward claims count too, even with no chores in the queue."""
    seed_data["reward_claims"] = [copy.deepcopy(PENDING_CLAIM)]
    await setup_choremander(seed_data)

    state = hass.states.get(BINARY_SENSOR)
    assert state.state == "on", "a claimed-but-unapproved reward still needs a parent"
    assert _payload(state) == {
        "pending_chore_completions": 0,
        "pending_reward_claims": 1,
        "total_pending": 1,
        "device_class": "problem",
        "icon": "mdi:bell-alert",
    }


async def test_total_pending_sums_both_queues_and_ignores_approved_rows(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """`total_pending` is the sum of the two counters, not of all stored records."""
    seed_data["completions"] = [
        copy.deepcopy(PENDING_COMPLETION),
        {
            "chore_id": "chore_bed",
            "child_id": "child_krishna",
            "completed_at": "2026-08-01T15:00:00Z",
            "approved": True,
            "approved_at": "2026-08-01T15:00:00Z",
            "points_awarded": 2,
            "id": "comp_done",
        },
    ]
    seed_data["reward_claims"] = [
        copy.deepcopy(PENDING_CLAIM),
        copy.deepcopy(APPROVED_CLAIM),
    ]
    await setup_choremander(seed_data)

    state = hass.states.get(BINARY_SENSOR)
    assert state.state == "on"
    assert state.attributes["pending_chore_completions"] == 1, (
        "the already-approved completion must not be counted as outstanding work"
    )
    assert state.attributes["pending_reward_claims"] == 1, (
        "the already-approved claim must not be counted either"
    )
    assert state.attributes["total_pending"] == 2


async def test_completing_an_approval_gated_chore_flips_the_sensor_on_live(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """The sensor tracks the coordinator, so it reacts without a restart."""
    await setup_choremander(seed_data)
    coordinator: ChoremanderCoordinator = hass.data[DOMAIN][ENTRY_ID]
    assert hass.states.get(BINARY_SENSOR).state == "off"

    await coordinator.async_complete_chore("chore_homework", "child_krishna")
    await hass.async_block_till_done()

    assert hass.states.get(BINARY_SENSOR).state == "on", (
        "Do Homework requires approval, so completing it must raise the pending flag"
    )
    assert hass.states.get(BINARY_SENSOR).attributes["total_pending"] == 1


async def test_completing_an_auto_approved_chore_leaves_the_sensor_off(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """Chores that pay out immediately never enter the approval queue."""
    await setup_choremander(seed_data)
    coordinator: ChoremanderCoordinator = hass.data[DOMAIN][ENTRY_ID]

    await coordinator.async_complete_chore("chore_bed", "child_krishna")
    await hass.async_block_till_done()

    state = hass.states.get(BINARY_SENSOR)
    assert state.state == "off", (
        "Make Bed does not require approval, so it must not ask a parent for anything"
    )
    assert state.attributes["pending_chore_completions"] == 0


async def test_device_class_is_problem_so_the_ui_renders_it_as_an_alert(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """PROBLEM gives the on-state its red 'needs attention' treatment."""
    entry = await setup_choremander(seed_data)
    coordinator: ChoremanderCoordinator = hass.data[DOMAIN][ENTRY_ID]

    entity = HasPendingApprovalsBinarySensor(coordinator, entry)
    assert entity.device_class is BinarySensorDeviceClass.PROBLEM
    assert hass.states.get(BINARY_SENSOR).attributes["device_class"] == "problem"


async def test_unique_id_is_scoped_to_the_config_entry(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """The unique id is the registry identity - changing it orphans the entity."""
    entry = await setup_choremander(seed_data)
    coordinator: ChoremanderCoordinator = hass.data[DOMAIN][ENTRY_ID]

    assert (
        HasPendingApprovalsBinarySensor(coordinator, entry).unique_id
        == f"{ENTRY_ID}_has_pending_approvals"
    )
    assert (
        er.async_get(hass).async_get(BINARY_SENSOR).unique_id == f"{ENTRY_ID}_has_pending_approvals"
    )


async def test_the_binary_sensor_hangs_off_the_shared_choremander_device(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """It shares the one device with the sensors and buttons."""
    await setup_choremander(seed_data)

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, ENTRY_ID)})
    assert device is not None, "no device was registered for the Choremander config entry"
    assert device.name == "Choremander"
    assert device.manufacturer == "Choremander"
    assert device.model == "Family Chore Manager"

    registry_entry = er.async_get(hass).async_get(BINARY_SENSOR)
    assert registry_entry.device_id == device.id


async def test_the_platform_creates_exactly_one_binary_sensor(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """No per-child binary sensors exist - there is one global flag."""
    await setup_choremander(seed_data)

    entities = [
        entity
        for entity in er.async_entries_for_config_entry(er.async_get(hass), ENTRY_ID)
        if entity.domain == "binary_sensor"
    ]
    assert [entity.entity_id for entity in entities] == [BINARY_SENSOR], (
        "the binary sensor platform is deliberately a single integration-wide entity"
    )
