"""Tests for the Choremander button platform.

Two of Choremander's confirmed defects live in this platform and are pinned
here deliberately (see the "CURRENT BEHAVIOUR" comments below):

* defect #9  - the coordinator update listener body is literally ``pass``, so
  chores and rewards created after setup never grow a button.
* defect #10 - ``ClaimRewardButton`` is created for every child x every reward,
  ignoring the reward's ``assigned_to`` list.

These tests assert today's behaviour so that a future fix shows up as a
deliberate, test-visible change rather than a silent one.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any
from unittest.mock import AsyncMock, Mock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander import button as button_module
from custom_components.choremander.button import (
    ApproveChoreButton,
    ClaimRewardButton,
    CompleteChoreButton,
    RejectChoreButton,
)
from custom_components.choremander.const import DOMAIN
from custom_components.choremander.coordinator import ChoremanderCoordinator
from tests.unit.dev_seed import DEV_SEED as _COMMITTED_SEED

SEED_ENTRY_ID = "seed_entry"

# The committed dev seed, from `tests/unit/dev_seed.py`. It is deliberately not
# read from `dev/config/.storage/` at test time: the `choremander-dev` container
# mounts that directory read-write and rewrites points, completions and claims
# while it runs, which would make every number below depend on whether a
# developer happens to have the container up. The two lifetime counters below
# are the values the seed had when these expectations were derived.
#
# 2 children / 8 chores / 5 rewards. The pricing consequences of this exact data
# are worked through in the test plan, section 4:
#   Krishna 15.0 pts/day (bed 2 + teeth_am 1 + feed_dog 3 + homework 5 +
#                         teeth_pm 1 + tidy_room 3)
#   Radha   13.0 pts/day (bed 2 + teeth_am 1 + water_plants 2 + piano 4 +
#                         teeth_pm 1 + tidy_room 3)
DEV_SEED: dict[str, Any] = deepcopy(_COMMITTED_SEED)
DEV_SEED["children"][0].update({"total_points_earned": 64, "total_chores_completed": 5})
DEV_SEED["children"][1].update({"total_points_earned": 13, "total_chores_completed": 3})

# Krishna is assigned 6 of the 8 seeded chores; water_plants and practice_piano
# belong to Radha, and vice versa for feed_dog and homework.
KRISHNA_CHORE_BUTTONS = {
    "button.krishna_complete_make_bed",
    "button.krishna_complete_brush_teeth_morning",
    "button.krishna_complete_feed_the_dog",
    "button.krishna_complete_do_homework",
    "button.krishna_complete_brush_teeth_evening",
    "button.krishna_complete_tidy_room",
}
RADHA_CHORE_BUTTONS = {
    "button.radha_complete_make_bed",
    "button.radha_complete_brush_teeth_morning",
    "button.radha_complete_water_the_plants",
    "button.radha_complete_practice_piano",
    "button.radha_complete_brush_teeth_evening",
    "button.radha_complete_tidy_room",
}
CLAIM_BUTTONS = {
    "button.krishna_claim_30_min_screen_time",
    "button.krishna_claim_new_video_game",
    "button.krishna_claim_new_book",
    "button.krishna_claim_family_movie_night",
    "button.krishna_claim_trip_to_the_zoo",
    "button.radha_claim_30_min_screen_time",
    "button.radha_claim_new_video_game",
    "button.radha_claim_new_book",
    "button.radha_claim_family_movie_night",
    "button.radha_claim_trip_to_the_zoo",
}


@pytest.fixture
async def seeded_entry(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    bypass_frontend_registration: None,
) -> ConfigEntry:
    """Set Choremander up over a deep copy of the frozen dev seed.

    The seed is deep-copied because ``to_dict``/``from_dict`` alias lists - a
    mutating test would otherwise contaminate the module-level constant.
    """
    hass_storage[f"choremander.storage.{SEED_ENTRY_ID}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"choremander.storage.{SEED_ENTRY_ID}",
        "data": deepcopy(DEV_SEED),
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Choremander",
        data={"points_name": "Stars", "points_icon": "mdi:star"},
        entry_id=SEED_ENTRY_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id), (
        "the seeded config entry failed to set up"
    )
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def seeded_coordinator(hass: HomeAssistant, seeded_entry: ConfigEntry) -> ChoremanderCoordinator:
    """The live coordinator sitting on top of the dev seed."""
    return hass.data[DOMAIN][seeded_entry.entry_id]


@pytest.fixture(autouse=True)
def quiet_pricing_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Silence the four WARNING lines the pricing engine emits per call."""
    caplog.set_level(logging.ERROR, logger="custom_components.choremander.coordinator")


def _set_points(coordinator: ChoremanderCoordinator, child_id: str, points: int) -> None:
    """Write a child's balance straight into storage (no save, no refresh)."""
    for child in coordinator.storage._data["children"]:
        if child["id"] == child_id:
            child["points"] = points
            return
    raise AssertionError(f"no seeded child with id {child_id}")


def _button_entity_ids(hass: HomeAssistant) -> set[str]:
    return {state.entity_id for state in hass.states.async_all("button")}


def test_frozen_seed_has_the_shape_every_assertion_below_assumes() -> None:
    """Guard the constant: 2 children, 8 chores, 5 rewards and a clean history."""
    assert len(DEV_SEED["children"]) == 2
    assert len(DEV_SEED["chores"]) == 8
    assert len(DEV_SEED["rewards"]) == 5
    assert DEV_SEED["completions"] == [], (
        "the frozen seed starts with no completion history so daily-limit checks "
        "cannot depend on what today's date happens to be"
    )
    assert DEV_SEED["reward_claims"] == []


async def test_dev_seed_produces_twenty_two_buttons_filtered_by_chore_assignment(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """Each child gets a button for their 6 chores plus all 5 rewards."""
    entity_ids = _button_entity_ids(hass)

    expected = KRISHNA_CHORE_BUTTONS | RADHA_CHORE_BUTTONS | CLAIM_BUTTONS
    assert entity_ids == expected, (
        "the button inventory drifted from 6 chore + 5 reward buttons per child; "
        f"unexpected={sorted(entity_ids - expected)} missing={sorted(expected - entity_ids)}"
    )
    assert len(entity_ids) == 22, (
        f"expected 22 buttons for 2 children on the dev seed, got {len(entity_ids)}"
    )


async def test_chore_buttons_are_not_created_for_chores_assigned_to_other_children(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """Complete-chore buttons honour ``chore.assigned_to``."""
    entity_ids = _button_entity_ids(hass)

    assert "button.krishna_complete_water_the_plants" not in entity_ids, (
        "Water the Plants is assigned to Radha only, so Krishna must not get a button"
    )
    assert "button.krishna_complete_practice_piano" not in entity_ids, (
        "Practice Piano is assigned to Radha only, so Krishna must not get a button"
    )
    assert "button.radha_complete_feed_the_dog" not in entity_ids, (
        "Feed the Dog is assigned to Krishna only, so Radha must not get a button"
    )
    assert "button.radha_complete_do_homework" not in entity_ids, (
        "Do Homework is assigned to Krishna only, so Radha must not get a button"
    )


async def test_claim_buttons_are_created_for_rewards_assigned_to_another_child(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """CURRENT BEHAVIOUR (defect #10, see test plan section 6.2).

    The reward loop in ``async_setup_entry`` has no ``assigned_to`` filter, so
    Radha gets a claim button for Krishna's personal reward and vice versa.
    This test pins today's behaviour so a future fix is a deliberate change.
    """
    game = seeded_coordinator.get_reward("reward_krishna_game")
    book = seeded_coordinator.get_reward("reward_radha_book")
    assert game.assigned_to == ["child_krishna"], "seed drifted: game is Krishna-only"
    assert book.assigned_to == ["child_radha"], "seed drifted: book is Radha-only"

    entity_ids = _button_entity_ids(hass)
    assert "button.radha_claim_new_video_game" in entity_ids, (
        "defect #10 no longer reproduces: claim buttons now respect assigned_to"
    )
    assert "button.krishna_claim_new_book" in entity_ids, (
        "defect #10 no longer reproduces: claim buttons now respect assigned_to"
    )


async def test_chores_added_after_setup_never_get_a_button(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """CURRENT BEHAVIOUR (defect #9, see test plan section 6.2).

    The coordinator update listener registered by ``async_setup_entry`` has an
    empty body, so nothing ever creates entities for data added later. A user
    must reload the integration before a new chore becomes pressable.
    """
    before = _button_entity_ids(hass)

    await seeded_coordinator.async_add_chore(name="Sweep the Porch", points=4)
    await hass.async_block_till_done()

    assert {c.name for c in seeded_coordinator.storage.get_chores()} >= {"Sweep the Porch"}, (
        "the new chore was not persisted, so this test proves nothing"
    )
    assert _button_entity_ids(hass) == before, (
        "defect #9 no longer reproduces: a chore added after setup now yields buttons"
    )


async def test_rewards_added_after_setup_never_get_a_button(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """CURRENT BEHAVIOUR (defect #9, see test plan section 6.2)."""
    before = _button_entity_ids(hass)

    await seeded_coordinator.async_add_reward(name="Ice Cream", cost=20)
    await hass.async_block_till_done()

    assert any(r.name == "Ice Cream" for r in seeded_coordinator.storage.get_rewards()), (
        "the new reward was not persisted, so this test proves nothing"
    )
    assert _button_entity_ids(hass) == before, (
        "defect #9 no longer reproduces: a reward added after setup now yields buttons"
    )


async def test_coordinator_refresh_never_calls_add_entities_a_second_time(
    hass: HomeAssistant,
    seeded_entry: ConfigEntry,
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """CURRENT BEHAVIOUR (defect #9, see test plan section 6.2).

    Drives ``async_setup_entry`` directly with a spy so the empty listener body
    is visible: entities are handed over exactly once, at setup.
    """
    add_entities = Mock()
    await button_module.async_setup_entry(hass, seeded_entry, add_entities)

    assert add_entities.call_count == 1, "setup must hand entities over exactly once"
    assert len(add_entities.call_args[0][0]) == 22, (
        f"setup should build 22 buttons for the dev seed, got {len(add_entities.call_args[0][0])}"
    )

    await seeded_coordinator.async_add_chore(name="Rake Leaves", points=1)
    await seeded_coordinator.async_refresh()
    await hass.async_block_till_done()

    assert add_entities.call_count == 1, (
        "defect #9 no longer reproduces: the update listener now adds entities"
    )


async def test_complete_chore_button_reports_the_chore_it_will_complete(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Attributes mirror the live chore, not the values captured at construction."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_homework")
    entity = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    assert entity.extra_state_attributes == {
        "child_id": "child_krishna",
        "child_name": "Krishna",
        "chore_id": "chore_homework",
        "chore_name": "Do Homework",
        "points": 5,
        "requires_approval": True,
    }, "complete-chore attributes drifted from the seeded Do Homework chore"
    assert entity.name == "Krishna: Complete Do Homework", (
        "the button label should read '<child>: Complete <chore>'"
    )


async def test_complete_chore_button_icon_is_always_check_circle(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """``Chore`` has no ``icon`` field, so the getattr branch is dead code."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_bed")
    entity = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    assert not hasattr(chore, "icon"), (
        "Chore grew an icon field; the button's getattr fallback is now live and "
        "this test must be rewritten"
    )
    assert entity.icon == "mdi:check-circle", "chore buttons should show a check circle"

    await seeded_coordinator.async_remove_chore("chore_bed")
    assert entity.icon == "mdi:check-circle", (
        "a button whose chore was deleted should still fall back to a check circle"
    )


async def test_complete_chore_button_for_a_deleted_chore_reports_no_attributes(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """A dangling button degrades to ``{}`` rather than raising."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_bed")
    entity = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    await seeded_coordinator.async_remove_chore("chore_bed")
    assert entity.extra_state_attributes == {}, (
        "a button pointing at a deleted chore must expose no attributes"
    )

    await seeded_coordinator.async_remove_child("child_krishna")
    assert entity.extra_state_attributes == {}, (
        "a button pointing at a deleted child must expose no attributes"
    )


async def test_pressing_a_chore_button_passes_chore_id_before_child_id(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """``async_complete_chore(chore_id, child_id)`` - order matters."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_bed")
    entity = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    seeded_coordinator.async_complete_chore = AsyncMock()
    await entity.async_press()

    seeded_coordinator.async_complete_chore.assert_awaited_once_with("chore_bed", "child_krishna")


async def test_pressing_a_chore_button_swallows_valueerror_and_logs_it(
    seeded_coordinator: ChoremanderCoordinator,
    seeded_entry: ConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Daily-limit and not-found errors must not bubble out of a button press."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_bed")
    entity = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    seeded_coordinator.async_complete_chore = AsyncMock(
        side_effect=ValueError("Daily limit reached")
    )
    with caplog.at_level(logging.ERROR, logger="custom_components.choremander.button"):
        assert await entity.async_press() is None, (
            "a rejected completion should return None, not raise"
        )

    assert "Failed to complete chore: Daily limit reached" in caplog.text, (
        "a swallowed ValueError must still be logged at ERROR for the user to see"
    )


async def test_pressing_a_chore_button_lets_non_valueerror_failures_propagate(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Only ``ValueError`` is caught; genuine bugs are not hidden."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_bed")
    entity = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    seeded_coordinator.async_complete_chore = AsyncMock(side_effect=KeyError("boom"))
    with pytest.raises(KeyError):
        await entity.async_press()


async def test_claim_button_prices_the_reward_dynamically_per_child(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Krishna earns 15 pts/day; 30 Min Screen Time targets 7 days -> 105."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_screen")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    assert entity.extra_state_attributes == {
        "child_id": "child_krishna",
        "child_name": "Krishna",
        "reward_id": "reward_screen",
        "reward_name": "30 Min Screen Time",
        "cost": 105,
        "override_cost": 10,
        "is_dynamic": True,
        "child_points": 25,
        "can_afford": False,
        "points_needed": 80,
    }, (
        "Krishna's screen-time price should be 15.0 pts/day x 7 days = 105, with the "
        "static cost of 10 reported only as override_cost"
    )
    assert entity.name == "Krishna: Claim 30 Min Screen Time"


async def test_claim_button_reports_the_manual_cost_when_override_is_set(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """An override reward short-circuits the chore math entirely."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_krishna_game")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    attrs = entity.extra_state_attributes
    assert attrs["cost"] == 50, "an override reward must charge its manual cost of 50"
    assert attrs["override_cost"] == 50
    assert attrs["is_dynamic"] is False, "is_dynamic is the inverse of override_point_value"
    assert attrs["points_needed"] == 25, "Krishna has 25 of the 50 points needed"


async def test_claim_button_falls_back_to_the_manual_cost_for_an_unpriced_child(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Radha is not in the override reward's price dict, so ``reward.cost`` wins."""
    radha = seeded_coordinator.get_child("child_radha")
    reward = seeded_coordinator.get_reward("reward_krishna_game")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, radha, reward)

    costs = seeded_coordinator.calculate_dynamic_reward_costs(reward)
    assert "child_radha" not in costs, (
        "the override price dict should only contain the assigned child"
    )
    assert entity.extra_state_attributes["cost"] == 50, (
        "an unassigned child should be quoted the reward's manual cost"
    )


async def test_claim_button_is_available_exactly_at_the_effective_cost(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """``child.points >= effective_cost`` - the boundary is inclusive."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_screen")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    _set_points(seeded_coordinator, "child_krishna", 105)
    assert entity.available is True, "105 points must afford a 105 point reward"
    assert entity.extra_state_attributes["points_needed"] == 0
    assert entity.extra_state_attributes["can_afford"] is True

    _set_points(seeded_coordinator, "child_krishna", 104)
    assert entity.available is False, "104 points must not afford a 105 point reward"
    assert entity.extra_state_attributes["points_needed"] == 1

    _set_points(seeded_coordinator, "child_krishna", 200)
    assert entity.extra_state_attributes["points_needed"] == 0, (
        "points_needed is clamped at 0 by max(0, ...) once the child can afford it"
    )


async def test_claim_button_stays_available_when_the_coordinator_update_failed(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """``ClaimRewardButton`` overrides ``available`` and ignores the base class."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_screen")
    chore = seeded_coordinator.get_chore("chore_bed")
    claim = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)
    complete = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    _set_points(seeded_coordinator, "child_krishna", 105)
    seeded_coordinator.last_update_success = False

    assert claim.available is True, (
        "the claim button ignores CoordinatorEntity.available and looks only at points"
    )
    assert complete.available is False, (
        "the complete button inherits CoordinatorEntity.available and should go "
        "unavailable after a failed refresh"
    )


async def test_claim_button_for_a_deleted_reward_is_unavailable_without_pricing_it(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """The ``if not child or not reward`` guard short-circuits before the math."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_screen")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    await seeded_coordinator.async_remove_reward("reward_screen")
    seeded_coordinator.calculate_dynamic_reward_costs = Mock(
        side_effect=AssertionError("pricing must not run for a deleted reward")
    )

    assert entity.available is False, "a button for a deleted reward must be unavailable"
    assert entity.extra_state_attributes == {}, (
        "a button for a deleted reward must expose no attributes"
    )
    assert entity.icon == "mdi:gift", (
        "a button for a deleted reward should fall back to the generic gift icon"
    )


async def test_claim_button_icon_follows_the_reward(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Unlike chores, rewards do carry an icon."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_screen")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    assert entity.icon == "mdi:television", "the claim button should render the reward's own icon"


async def test_pressing_a_claim_button_passes_reward_id_before_child_id(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """``async_claim_reward(reward_id, child_id)`` - order matters."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_screen")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    seeded_coordinator.async_claim_reward = AsyncMock()
    await entity.async_press()

    seeded_coordinator.async_claim_reward.assert_awaited_once_with("reward_screen", "child_krishna")


async def test_pressing_an_unaffordable_claim_button_swallows_valueerror_and_logs_it(
    seeded_coordinator: ChoremanderCoordinator,
    seeded_entry: ConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Insufficient funds is logged, not raised, so the UI does not error out."""
    child = seeded_coordinator.get_child("child_krishna")
    reward = seeded_coordinator.get_reward("reward_screen")
    entity = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    with caplog.at_level(logging.ERROR, logger="custom_components.choremander.button"):
        assert await entity.async_press() is None, (
            "an unaffordable claim should return None, not raise"
        )

    assert "Failed to claim reward: Not enough points. Need 105, have 25" in caplog.text, (
        "the swallowed error should name the dynamic price (105) and the balance (25)"
    )
    assert seeded_coordinator.get_child("child_krishna").points == 25, (
        "a failed claim must not move any points"
    )


async def test_button_unique_ids_are_namespaced_by_entry_child_and_target(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Unique ids must survive a rename of the child, chore or reward."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_bed")
    reward = seeded_coordinator.get_reward("reward_screen")

    complete = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)
    claim = ClaimRewardButton(seeded_coordinator, seeded_entry, child, reward)

    assert complete.unique_id == "seed_entry_child_krishna_chore_bed_complete"
    assert claim.unique_id == "seed_entry_child_krishna_reward_screen_claim"


async def test_all_buttons_report_a_single_choremander_device(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Every button hangs off the one service device for the entry."""
    child = seeded_coordinator.get_child("child_krishna")
    chore = seeded_coordinator.get_chore("chore_bed")
    entity = CompleteChoreButton(seeded_coordinator, seeded_entry, child, chore)

    assert entity.device_info["identifiers"] == {(DOMAIN, "seed_entry")}
    assert entity.device_info["name"] == "Choremander"
    assert entity.device_info["manufacturer"] == "Choremander"
    assert entity.device_info["model"] == "Family Chore Manager"


async def test_approve_and_reject_chore_buttons_are_never_instantiated_by_setup(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """Approval happens through the Lovelace card, not through button entities."""
    entity_ids = _button_entity_ids(hass)

    assert not any(e.endswith("_approve") or e.endswith("_reject") for e in entity_ids), (
        "ApproveChoreButton/RejectChoreButton are dead code; setup must not create them"
    )
    assert not any(e.startswith("button.approve_") for e in entity_ids)
    assert not any(e.startswith("button.reject_") for e in entity_ids)


async def test_approve_chore_button_forwards_the_completion_id_unguarded(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Dead code, unit-tested directly: ``async_press`` has no try/except."""
    entity = ApproveChoreButton(seeded_coordinator, seeded_entry, "6cc898f4", "Krishna", "Make Bed")

    assert entity.unique_id == "seed_entry_6cc898f4_approve"
    assert entity.name == "Approve: Krishna - Make Bed"
    assert entity.icon == "mdi:check-circle"

    seeded_coordinator.async_approve_chore = AsyncMock()
    await entity.async_press()
    seeded_coordinator.async_approve_chore.assert_awaited_once_with("6cc898f4")

    seeded_coordinator.async_approve_chore = AsyncMock(side_effect=ValueError("nope"))
    with pytest.raises(ValueError, match="nope"):
        await entity.async_press()


async def test_reject_chore_button_forwards_the_completion_id_unguarded(
    seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Dead code, unit-tested directly: ``async_press`` has no try/except."""
    entity = RejectChoreButton(seeded_coordinator, seeded_entry, "6cc898f4", "Krishna", "Make Bed")

    assert entity.unique_id == "seed_entry_6cc898f4_reject"
    assert entity.name == "Reject: Krishna - Make Bed"
    assert entity.icon == "mdi:close-circle"

    seeded_coordinator.async_reject_chore = AsyncMock()
    await entity.async_press()
    seeded_coordinator.async_reject_chore.assert_awaited_once_with("6cc898f4")

    seeded_coordinator.async_reject_chore = AsyncMock(side_effect=ValueError("nope"))
    with pytest.raises(ValueError, match="nope"):
        await entity.async_press()


async def test_pressing_a_chore_button_through_home_assistant_awards_points(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """End-to-end through the entity platform: Make Bed needs no approval."""
    assert seeded_coordinator.get_child("child_krishna").points == 25

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": "button.krishna_complete_make_bed"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert seeded_coordinator.get_child("child_krishna").points == 27, (
        "Make Bed is worth 2 points and requires no approval, so pressing its "
        "button should credit Krishna immediately"
    )
