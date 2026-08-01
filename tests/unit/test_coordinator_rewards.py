"""Reward claiming, approval, rejection and the manual points services.

The pricing figures asserted here (105, 91, 70, 165, ...) come from executing
the real dynamic-pricing algorithm against the development data set - see test
plan §4. They are literals on purpose: any drift in the formula should break
these tests loudly.

`DEV_SEED` below is built from the committed seed frozen in
`tests/unit/dev_seed.py`, plus the lifetime counters and the one pending claim
these tests need. It is deliberately *not* read from
`dev/config/.storage/choremander.storage.choremander_dev_entry`: a running
`docker-compose.dev.yml` container rewrites that file as the app is used, which
would make every pricing assertion non-deterministic. The `completions` list is
empty so no daily-limit or pending-approval state leaks into these tests.

Three assertions pin behaviour that is wrong; each is marked
`CURRENT BEHAVIOUR (defect #N)`.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander.const import DOMAIN
from custom_components.choremander.coordinator import ChoremanderCoordinator
from tests.unit.dev_seed import DEV_SEED as _COMMITTED_SEED

# The one reward claim that ships in the development seed: New Video Game,
# claimed by Krishna, still awaiting a parent's decision.
SEED_CLAIM_ID = "5c49cf0c"

DEV_SEED: dict[str, Any] = deepcopy(_COMMITTED_SEED)
DEV_SEED["children"][0].update({"total_points_earned": 64, "total_chores_completed": 5})
DEV_SEED["children"][1].update({"total_points_earned": 13, "total_chores_completed": 3})
DEV_SEED["reward_claims"] = [
    {
        "reward_id": "reward_krishna_game",
        "child_id": "child_krishna",
        "claimed_at": "2026-08-01T19:49:13.641757Z",
        "approved": False,
        "approved_at": None,
        "id": SEED_CLAIM_ID,
    }
]


@pytest.fixture
async def seeded_coordinator(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> ChoremanderCoordinator:
    """A coordinator over the 2-child / 8-chore / 5-reward development data set.

    Krishna earns 15.0 points/day, Radha 13.0 - test plan §4.1. `DEV_SEED` is
    deep-copied because `to_dict()`/`from_dict()` alias lists, so a mutating
    test would otherwise contaminate the module-level constant.
    """
    hass_storage["choremander.storage.seed_entry"] = {
        "version": 1,
        "minor_version": 1,
        "key": "choremander.storage.seed_entry",
        "data": deepcopy(DEV_SEED),
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Choremander",
        data={"points_name": "Stars", "points_icon": "mdi:star"},
        entry_id="seed_entry",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN]["seed_entry"]


async def _set_balance(coordinator: ChoremanderCoordinator, child_id: str, points: int) -> None:
    """Give a child a specific opening balance without touching their totals."""
    child = coordinator.get_child(child_id)
    child.points = points
    await coordinator.async_update_child(child)


def _claim_ids(coordinator: ChoremanderCoordinator) -> list[str]:
    """The claim ids currently persisted in the store."""
    return [c.id for c in coordinator.storage.get_reward_claims()]


# ---------------------------------------------------------------------------
# Claiming a reward
# ---------------------------------------------------------------------------


async def test_claiming_charges_the_dynamic_price_not_the_stored_cost(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """30 Min Screen Time stores `cost: 10` but really costs Krishna 105."""
    await _set_balance(seeded_coordinator, "child_krishna", 200)

    reward = seeded_coordinator.get_reward("reward_screen")
    assert reward.cost == 10, "seed precondition: the stored manual cost is 10"
    assert seeded_coordinator.calculate_dynamic_reward_costs(reward) == {
        "child_krishna": 105,
        "child_radha": 91,
    }, "15.0 and 13.0 points/day over a 7-day goal"

    await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")

    assert seeded_coordinator.get_child("child_krishna").points == 95, (
        "Krishna should be charged the dynamic 105, not the stale manual 10"
    )
    assert SEED_CLAIM_ID in _claim_ids(seeded_coordinator), (
        "the pre-existing claim for a different reward must be left alone"
    )
    assert len(_claim_ids(seeded_coordinator)) == 2, "the new claim should be recorded"


async def test_claiming_without_enough_points_reports_the_effective_cost(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """The error message quotes the dynamic price, which is what the child owes."""
    assert seeded_coordinator.get_child("child_krishna").points == 25, (
        "seed precondition: Krishna starts on 25"
    )

    with pytest.raises(ValueError) as excinfo:
        await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")

    assert str(excinfo.value) == "Not enough points. Need 105, have 25", (
        "the shortfall message must quote the dynamic 105, not the stored cost of 10"
    )
    assert _claim_ids(seeded_coordinator) == [SEED_CLAIM_ID], (
        "a refused claim must not be persisted"
    )


async def test_claiming_with_exactly_the_price_succeeds_and_zeroes_the_balance(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """The affordability check is `<`, so paying your last point is allowed."""
    await _set_balance(seeded_coordinator, "child_krishna", 105)

    await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")

    assert seeded_coordinator.get_child("child_krishna").points == 0, (
        "a child holding exactly the price should be able to spend down to zero"
    )


async def test_claiming_a_reward_assigned_to_someone_else_falls_back_to_manual_cost(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """CURRENT BEHAVIOUR (defect #11, see test plan §6.2) - no assignment check.

    `costs` only covers the assigned children and the lookup is
    `costs.get(child_id, reward.cost)`. An unassigned child is therefore not
    refused - they are silently charged the raw manual cost, which for a
    dynamically-priced reward is a meaningless number.
    """
    reward = await seeded_coordinator.async_add_reward(
        name="Radha Only Treat",
        cost=7,
        assigned_to=["child_radha"],
        days_to_goal=7,
    )
    assert seeded_coordinator.calculate_dynamic_reward_costs(reward) == {"child_radha": 91}, (
        "the priced dict covers only the assigned child (13.0/day x 7 days)"
    )

    await seeded_coordinator.async_claim_reward(reward.id, "child_krishna")

    assert seeded_coordinator.get_child("child_krishna").points == 18, (
        "Krishna was allowed to claim Radha's reward and paid the raw manual cost "
        "of 7 (25 - 7), rather than being refused or charged a dynamic price"
    )


async def test_claiming_an_unknown_reward_is_rejected_before_the_child_is_checked(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """The reward is resolved first, so a doubly-bad call blames the reward."""
    with pytest.raises(ValueError, match="Reward ghost_reward not found"):
        await seeded_coordinator.async_claim_reward("ghost_reward", "ghost_child")


async def test_claiming_a_real_reward_for_an_unknown_child_is_rejected(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """Once the reward resolves, an unknown child is the reported problem."""
    with pytest.raises(ValueError, match="Child ghost_child not found"):
        await seeded_coordinator.async_claim_reward("reward_screen", "ghost_child")


# ---------------------------------------------------------------------------
# Approving a claim
# ---------------------------------------------------------------------------


async def test_approving_a_claim_moves_no_points_and_is_idempotent(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """Points leave the balance at claim time, so approval only flips flags."""
    await seeded_coordinator.async_approve_reward(SEED_CLAIM_ID)

    claim = next(c for c in seeded_coordinator.storage.get_reward_claims() if c.id == SEED_CLAIM_ID)
    assert claim.approved is True, "approval must mark the claim approved"
    assert claim.approved_at is not None, "approval must stamp a decision time"
    assert seeded_coordinator.get_child("child_krishna").points == 25, (
        "approving a claim must not move points - they were deducted when claimed"
    )

    await seeded_coordinator.async_approve_reward(SEED_CLAIM_ID)

    assert seeded_coordinator.get_child("child_krishna").points == 25, (
        "unlike chore approval, approving a claim twice is harmless"
    )
    assert seeded_coordinator.storage.get_pending_reward_claims() == [], (
        "the approved claim must leave the pending queue"
    )


async def test_approving_or_rejecting_an_unknown_claim_id_is_a_silent_noop(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """Both handlers return from inside the loop, so a miss does nothing at all."""
    with patch.object(
        seeded_coordinator.storage,
        "async_save",
        wraps=seeded_coordinator.storage.async_save,
    ) as save:
        await seeded_coordinator.async_approve_reward("no_such_claim")
        await seeded_coordinator.async_reject_reward("no_such_claim")

    assert save.call_count == 0, "neither handler should persist anything on a miss"
    assert _claim_ids(seeded_coordinator) == [SEED_CLAIM_ID], "the real claim survives"
    assert seeded_coordinator.get_child("child_krishna").points == 25, "no balance may move"


# ---------------------------------------------------------------------------
# Rejecting a claim
# ---------------------------------------------------------------------------


async def test_rejecting_a_claim_refunds_the_points_and_deletes_the_claim(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """With nothing else changing, reject is a clean inverse of claim."""
    await _set_balance(seeded_coordinator, "child_krishna", 200)
    claim = await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")
    assert seeded_coordinator.get_child("child_krishna").points == 95, (
        "precondition: the claim charged 105"
    )

    await seeded_coordinator.async_reject_reward(claim.id)

    assert seeded_coordinator.get_child("child_krishna").points == 200, (
        "rejecting an otherwise untouched claim must restore the balance exactly"
    )
    assert claim.id not in _claim_ids(seeded_coordinator), (
        "a rejected claim is deleted outright rather than marked rejected"
    )
    stored_ids = [c.get("id") for c in seeded_coordinator.storage._data.get("reward_claims", [])]
    assert stored_ids == [SEED_CLAIM_ID], (
        "the raw store must have only the untouched pre-existing claim left; "
        "`async_reject_reward` edits `storage._data` directly because there is "
        "no `remove_reward_claim` method"
    )


async def test_rejecting_a_claim_reprices_it_and_can_destroy_points(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """CURRENT BEHAVIOUR (defect #3, see test plan §6.2) - the refund is recomputed.

    `async_reject_reward` calls `calculate_dynamic_reward_costs` again at
    rejection time instead of refunding what was actually charged, so any chore
    edit in between silently creates or destroys points. Here deleting Do
    Homework drops Krishna from 15.0 to 10.0 points/day, the screen-time price
    falls from 105 to 70, and he is refunded 35 less than he paid. This test
    pins today's behaviour so a future fix shows up as a deliberate change.
    """
    await _set_balance(seeded_coordinator, "child_krishna", 200)
    claim = await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")
    assert seeded_coordinator.get_child("child_krishna").points == 95, (
        "precondition: the claim charged 105"
    )

    await seeded_coordinator.async_remove_chore("chore_homework")
    reprice = seeded_coordinator.calculate_dynamic_reward_costs(
        seeded_coordinator.get_reward("reward_screen")
    )
    assert reprice["child_krishna"] == 70, (
        "losing the 5-point chore reprices screen time to 10.0/day x 7 days"
    )

    await seeded_coordinator.async_reject_reward(claim.id)

    assert seeded_coordinator.get_child("child_krishna").points == 165, (
        "Krishna paid 105 but is refunded only the new price of 70, permanently "
        "losing 35 points; a correct implementation would restore him to 200"
    )


async def test_rejecting_a_claim_whose_reward_was_deleted_refunds_nothing(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """The `if reward and child` guard skips the refund but not the deletion."""
    await _set_balance(seeded_coordinator, "child_krishna", 200)
    claim = await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")

    await seeded_coordinator.async_remove_reward("reward_screen")
    await seeded_coordinator.async_reject_reward(claim.id)

    assert seeded_coordinator.get_child("child_krishna").points == 95, (
        "with no reward left to price, the 105 that was charged is simply lost"
    )
    assert claim.id not in _claim_ids(seeded_coordinator), (
        "the claim is still deleted even though no refund was possible"
    )


# ---------------------------------------------------------------------------
# Manual points adjustments
# ---------------------------------------------------------------------------


async def test_adding_points_also_increments_the_lifetime_chore_count(
    coordinator: ChoremanderCoordinator,
) -> None:
    """CURRENT BEHAVIOUR (defect #4, see test plan §6.2) - a bonus fakes a chore.

    `async_add_points` routes through `_award_points`, which unconditionally
    does `total_chores_completed += 1`. Even a zero-point bonus inflates the
    child's lifetime chore count. This test pins today's behaviour so a future
    fix shows up as a deliberate change.
    """
    child = await coordinator.async_add_child("Radha")
    await _set_balance(coordinator, child.id, 15)

    await coordinator.async_add_points(child.id, 0, reason="just checking")

    after = coordinator.get_child(child.id)
    assert after.points == 15, "a zero-point bonus must not change the balance"
    assert after.total_points_earned == 0, "nor lifetime earnings"
    assert after.total_chores_completed == 1, (
        "but the lifetime chore count is incremented anyway - a manual points "
        "adjustment is recorded as though a chore had been done"
    )


async def test_adding_points_credits_the_balance_and_lifetime_earnings(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The ordinary bonus path."""
    child = await coordinator.async_add_child("Krishna")
    await _set_balance(coordinator, child.id, 25)

    await coordinator.async_add_points(child.id, 7, reason="Helped a sibling")

    after = coordinator.get_child(child.id)
    assert after.points == 32, "a 7-point bonus should take 25 to 32"
    assert after.total_points_earned == 7, "bonuses count towards lifetime earnings"


async def test_removing_points_clamps_at_zero_and_leaves_lifetime_totals_alone(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A penalty larger than the balance zeroes it rather than creating a debt."""
    child = await coordinator.async_add_child("Krishna")
    seeded = coordinator.get_child(child.id)
    seeded.points = 15
    seeded.total_points_earned = 64
    seeded.total_chores_completed = 5
    await coordinator.async_update_child(seeded)

    await coordinator.async_remove_points(child.id, 100, reason="broke a window")

    after = coordinator.get_child(child.id)
    assert after.points == 0, "removing 100 from 15 must clamp at 0, never -85"
    assert after.total_points_earned == 64, (
        "a penalty is not an un-earning; lifetime earnings stay put"
    )
    assert after.total_chores_completed == 5, "and the lifetime chore count stays put"


async def test_adding_and_removing_points_reject_an_unknown_child(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Both operations fail loudly rather than silently dropping the adjustment."""
    with pytest.raises(ValueError, match="Child nope not found"):
        await coordinator.async_add_points("nope", 5)

    with pytest.raises(ValueError, match="Child nope not found"):
        await coordinator.async_remove_points("nope", 5)


async def test_the_reason_for_a_points_adjustment_is_discarded(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`reason` is accepted by the API and by `services.yaml` but never stored.

    There is no audit trail for manual points adjustments.
    """
    child = await coordinator.async_add_child("Krishna")

    await coordinator.async_add_points(child.id, 5, reason="Helped a sibling")
    await coordinator.async_remove_points(child.id, 2, reason="Backchat")

    dumped = json.dumps(coordinator.storage._data)
    assert "Helped a sibling" not in dumped, (
        "the reason given for a bonus is thrown away, not persisted"
    )
    assert "Backchat" not in dumped, "the reason given for a penalty is thrown away, not persisted"
    assert coordinator.get_child(child.id).points == 3, (
        "the adjustments themselves still applied (0 + 5 - 2)"
    )
