"""Reward claiming, approval, rejection and the manual points services.

The pricing figures asserted here (108, 91, 73, 165, ...) come from executing
the real dynamic-pricing algorithm against the development data set - see test
plan §4. They are literals on purpose: any drift in the formula should break
these tests loudly. Three of the seed's chores now carry a schedule, so a chore
that only comes round twice a week is priced at twice a week; the figures moved
when that landed, and they should move again if the frequency ever stops
counting.

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
    """A coordinator over the 3-child / 11-chore / 5-reward development data set.

    Krishna earns 15+3/7 points/day, Radha 13.0 and Meera 23/7 - test plan §4.1.
    `DEV_SEED` is deep-copied because `to_dict()`/`from_dict()` alias lists, so a mutating
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
    """30 Min Screen Time stores `cost: 10` but really costs Krishna 108."""
    await _set_balance(seeded_coordinator, "child_krishna", 200)

    reward = seeded_coordinator.get_reward("reward_screen")
    assert reward.cost == 10, "seed precondition: the stored manual cost is 10"
    assert seeded_coordinator.calculate_dynamic_reward_costs(reward) == {
        "child_krishna": 108,
        "child_radha": 91,
        "child_meera": 23,
    }, "15+3/7, 13.0 and 23/7 points/day over a 7-day goal"

    await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")

    assert seeded_coordinator.get_child("child_krishna").points == 92, (
        "Krishna should be charged the dynamic 108, not the stale manual 10"
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

    assert str(excinfo.value) == "Not enough points. Need 108, have 25", (
        "the shortfall message must quote the dynamic 108, not the stored cost of 10"
    )
    assert _claim_ids(seeded_coordinator) == [SEED_CLAIM_ID], (
        "a refused claim must not be persisted"
    )


async def test_claiming_with_exactly_the_price_succeeds_and_zeroes_the_balance(
    seeded_coordinator: ChoremanderCoordinator,
) -> None:
    """The affordability check is `<`, so paying your last point is allowed."""
    await _set_balance(seeded_coordinator, "child_krishna", 108)

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
    assert seeded_coordinator.get_child("child_krishna").points == 92, (
        "precondition: the claim charged 108"
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
    Homework drops Krishna from 15+3/7 to 10+3/7 points/day, the screen-time
    price falls from 108 to 73, and he is refunded 35 less than he paid. This
    test pins today's behaviour so a future fix shows up as a deliberate change.
    """
    await _set_balance(seeded_coordinator, "child_krishna", 200)
    claim = await seeded_coordinator.async_claim_reward("reward_screen", "child_krishna")
    assert seeded_coordinator.get_child("child_krishna").points == 92, (
        "precondition: the claim charged 108"
    )

    await seeded_coordinator.async_remove_chore("chore_homework")
    reprice = seeded_coordinator.calculate_dynamic_reward_costs(
        seeded_coordinator.get_reward("reward_screen")
    )
    assert reprice["child_krishna"] == 73, (
        "losing the 5-point daily chore reprices screen time to (10+3/7)/day x 7 days"
    )

    await seeded_coordinator.async_reject_reward(claim.id)

    assert seeded_coordinator.get_child("child_krishna").points == 165, (
        "Krishna paid 108 but is refunded only the new price of 73, permanently "
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

    assert seeded_coordinator.get_child("child_krishna").points == 92, (
        "with no reward left to price, the 108 that was charged is simply lost"
    )
    assert claim.id not in _claim_ids(seeded_coordinator), (
        "the claim is still deleted even though no refund was possible"
    )


# ---------------------------------------------------------------------------
# Manual points adjustments
# ---------------------------------------------------------------------------


async def test_a_manual_bonus_is_not_counted_as_a_chore(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A points adjustment must not inflate the lifetime chore count.

    `async_add_points` routes through `_award_points`, which used to do
    `total_chores_completed += 1` unconditionally - so handing out a bonus, or
    even a zero-point adjustment, claimed a chore had been done. That number
    now drives the streak card's achievements, so it has to mean what it says.
    """
    child = await coordinator.async_add_child("Radha")
    await _set_balance(coordinator, child.id, 15)

    await coordinator.async_add_points(child.id, 0, reason="just checking")
    await coordinator.async_add_points(child.id, 7, reason="Helped a sibling")

    after = coordinator.get_child(child.id)
    assert after.points == 22, "a zero-point bonus is a no-op; the 7 applies"
    assert after.total_points_earned == 7, "only real points count as earned"
    assert after.total_chores_completed == 0, (
        "no chore was done, so the lifetime chore count must stay at zero"
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


async def test_the_reason_for_a_points_adjustment_is_recorded_in_the_ledger(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`reason` is accepted by the API and by `services.yaml`, and now stored.

    It used to be discarded, which left manual adjustments as unexplained
    movements in a child's balance.
    """
    child = await coordinator.async_add_child("Krishna")

    await coordinator.async_add_points(child.id, 5, reason="Helped a sibling")
    await coordinator.async_remove_points(child.id, 2, reason="Backchat")

    dumped = json.dumps(coordinator.storage._data)
    assert "Helped a sibling" in dumped, "the reason given for a bonus is persisted"
    assert "Backchat" in dumped, "the reason given for a penalty is persisted"
    assert coordinator.get_child(child.id).points == 3, (
        "the adjustments themselves still applied (0 + 5 - 2)"
    )

    ledger = coordinator.storage.get_points_transactions()
    assert [(t.event_type, t.points, t.reason) for t in ledger] == [
        ("points_added", 5, "Helped a sibling"),
        ("points_removed", -2, "Backchat"),
    ], "oldest first, with the sign of the movement and the reason kept apart"
