"""Chore completion, approval, rejection and daily-limit behaviour.

Everything here drives the *real* `ChoremanderCoordinator` over the real
`ChoremanderStorage`; the only test double is the in-memory `hass_storage`
that pytest-homeassistant-custom-component already substitutes for the
on-disk `Store`.

Several assertions deliberately pin behaviour that is wrong. Those are
marked `CURRENT BEHAVIOUR (defect #N)` so that fixing the source shows up
as a deliberate, reviewable test change rather than a silent regression.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import Child, Chore


@pytest.fixture
async def la_timezone(hass: HomeAssistant) -> None:
    """Pin the HA timezone to the one `docker-compose.dev.yml` uses.

    `async_complete_chore` compares *local* dates, so every daily-limit test
    that crosses a UTC midnight needs a deterministic offset.
    """
    await hass.config.async_set_time_zone("America/Los_Angeles")


async def _make_child(coordinator: ChoremanderCoordinator, name: str, points: int = 0) -> Child:
    """Add a child and give them an opening balance."""
    child = await coordinator.async_add_child(name)
    if points:
        child.points = points
        await coordinator.async_update_child(child)
    return coordinator.get_child(child.id)


async def _make_chore(coordinator: ChoremanderCoordinator, name: str, **kwargs: Any) -> Chore:
    """Add a chore, defaulting to the no-approval / 1-per-day shape."""
    kwargs.setdefault("points", 3)
    kwargs.setdefault("requires_approval", False)
    kwargs.setdefault("daily_limit", 1)
    return await coordinator.async_add_chore(name=name, **kwargs)


# ---------------------------------------------------------------------------
# Completing a chore
# ---------------------------------------------------------------------------


async def test_completing_a_no_approval_chore_awards_points_immediately(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A chore with `requires_approval=False` pays out on completion."""
    child = await _make_child(coordinator, "Krishna")
    chore = await _make_chore(coordinator, "Make Bed", points=3)

    completion = await coordinator.async_complete_chore(chore.id, child.id)

    assert completion.approved is True, (
        "a chore that needs no approval must be recorded as already approved"
    )
    assert completion.points_awarded == 3, (
        "the completion record must carry the points that were actually paid"
    )
    assert completion.approved_at is not None, (
        "self-approving completions must stamp an approval time"
    )

    after = coordinator.get_child(child.id)
    assert after.points == 3, "Krishna should be paid the chore's 3 points on the spot"
    assert after.total_points_earned == 3, "lifetime earnings must include the payout"
    assert after.total_chores_completed == 1, "the completion must count towards the lifetime total"


async def test_completing_an_approval_chore_awards_nothing_and_queues_it(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A chore with `requires_approval=True` pays nothing until a parent approves."""
    child = await _make_child(coordinator, "Krishna", points=25)
    chore = await _make_chore(coordinator, "Do Homework", points=5, requires_approval=True)

    completion = await coordinator.async_complete_chore(chore.id, child.id)

    assert completion.approved is False, "an approval-gated completion must start unapproved"
    assert completion.points_awarded == 0, "no points may be recorded before approval"

    after = coordinator.get_child(child.id)
    assert after.points == 25, "Krishna's balance must not move before a parent approves"
    assert after.total_chores_completed == 0, (
        "an unapproved completion must not inflate the lifetime chore count"
    )
    assert len(coordinator.storage.get_pending_completions()) == 1, (
        "the completion should be waiting in the approval queue"
    )


async def test_completing_a_chore_assigned_to_another_child_still_pays_out(
    coordinator: ChoremanderCoordinator,
) -> None:
    """There is no server-side assignment check.

    CURRENT BEHAVIOUR (defect #11, see test plan §6.2) - `async_complete_chore`
    never inspects `chore.assigned_to`, so the per-child button filtering in the
    UI is cosmetic only. Radha can bank Krishna's dog-feeding points.
    """
    krishna = await _make_child(coordinator, "Krishna")
    radha = await _make_child(coordinator, "Radha", points=15)
    dog = await _make_chore(coordinator, "Feed the Dog", points=3, assigned_to=[krishna.id])
    assert coordinator.get_chore(dog.id).assigned_to == [krishna.id], (
        "precondition: dog duty is Krishna's alone"
    )

    await coordinator.async_complete_chore(dog.id, radha.id)

    assert coordinator.get_child(radha.id).points == 18, (
        "Radha started on 15 and was paid the 3 points for a chore assigned to Krishna"
    )
    assert coordinator.get_child(krishna.id).points == 0, (
        "the child the chore was actually assigned to earned nothing"
    )


async def test_completing_an_unknown_chore_is_rejected_before_the_child_is_checked(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The chore lookup happens first, so a doubly-bad call blames the chore."""
    with pytest.raises(ValueError, match="Chore ghost_chore not found"):
        await coordinator.async_complete_chore("ghost_chore", "ghost_child")


async def test_completing_a_real_chore_for_an_unknown_child_is_rejected(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Once the chore resolves, an unknown child is the reported problem."""
    chore = await _make_chore(coordinator, "Make Bed")

    with pytest.raises(ValueError, match="Child ghost_child not found"):
        await coordinator.async_complete_chore(chore.id, "ghost_child")


# ---------------------------------------------------------------------------
# Daily limits
# ---------------------------------------------------------------------------


async def test_daily_limit_counts_pending_and_approved_completions_alike(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Approving a completion does not free up a slot under the daily limit."""
    child = await _make_child(coordinator, "Krishna")
    chore = await _make_chore(coordinator, "Q", points=1, requires_approval=True, daily_limit=2)

    first = await coordinator.async_complete_chore(chore.id, child.id)
    await coordinator.async_complete_chore(chore.id, child.id)
    await coordinator.async_approve_chore(first.id)

    with pytest.raises(ValueError) as excinfo:
        await coordinator.async_complete_chore(chore.id, child.id)

    assert str(excinfo.value) == (
        "Daily limit reached for chore 'Q'. Already completed 2 time(s) today (limit: 2)"
    ), "one approved and one pending completion must together exhaust a limit of 2"


async def test_daily_limit_is_scoped_to_one_chore_and_one_child(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Exhausting a limit blocks only that (chore, child) pair."""
    krishna = await _make_child(coordinator, "Krishna")
    radha = await _make_child(coordinator, "Radha")
    bed = await _make_chore(coordinator, "Make Bed", points=2, daily_limit=1)
    teeth = await _make_chore(coordinator, "Brush Teeth", points=1, daily_limit=1)

    await coordinator.async_complete_chore(bed.id, krishna.id)

    with pytest.raises(ValueError, match="Daily limit reached"):
        await coordinator.async_complete_chore(bed.id, krishna.id)

    await coordinator.async_complete_chore(bed.id, radha.id)
    await coordinator.async_complete_chore(teeth.id, krishna.id)

    assert coordinator.get_child(radha.id).points == 2, (
        "Radha's own daily allowance for Make Bed must be untouched by Krishna's"
    )
    assert coordinator.get_child(krishna.id).points == 3, (
        "Krishna should still be able to earn the 1 point for a different chore"
    )


async def test_daily_limit_of_zero_blocks_the_very_first_completion(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The comparison is `>=`, so a limit of 0 means the chore is never doable."""
    child = await _make_child(coordinator, "Krishna")
    chore = await _make_chore(coordinator, "Impossible", daily_limit=0)

    with pytest.raises(ValueError) as excinfo:
        await coordinator.async_complete_chore(chore.id, child.id)

    assert str(excinfo.value) == (
        "Daily limit reached for chore 'Impossible'. Already completed 0 time(s) today (limit: 0)"
    ), "a zero limit must be reported as already-exhausted rather than allowing one go"
    assert coordinator.storage.get_completions() == [], "a blocked completion must not be persisted"


async def test_daily_limit_rolls_over_on_local_midnight_not_utc_midnight(
    hass: HomeAssistant,
    coordinator: ChoremanderCoordinator,
    la_timezone: None,
    freezer: Any,
) -> None:
    """ "Today" is the child's local day, not the UTC day.

    In America/Los_Angeles, 05:00Z and 06:00Z on 2 Aug are both still 1 Aug
    locally, so they share one daily allowance; 18:00Z is 2 Aug locally and
    starts a fresh one.
    """
    child = await _make_child(coordinator, "Krishna")
    chore = await _make_chore(coordinator, "Make Bed", points=2, daily_limit=1)

    freezer.move_to("2026-08-02T05:00:00+00:00")  # 2026-08-01 22:00 PDT
    await coordinator.async_complete_chore(chore.id, child.id)

    freezer.move_to("2026-08-02T06:00:00+00:00")  # 2026-08-01 23:00 PDT
    with pytest.raises(ValueError, match="Daily limit reached"):
        await coordinator.async_complete_chore(chore.id, child.id)

    freezer.move_to("2026-08-02T18:00:00+00:00")  # 2026-08-02 11:00 PDT
    await coordinator.async_complete_chore(chore.id, child.id)

    assert coordinator.get_child(child.id).points == 4, (
        "the local-day rollover should have allowed exactly two payouts of 2 points"
    )


# ---------------------------------------------------------------------------
# Approving a completion
# ---------------------------------------------------------------------------


async def test_approving_a_pending_completion_pays_the_chore_points(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The happy path: approval releases the withheld points."""
    child = await _make_child(coordinator, "Krishna", points=25)
    chore = await _make_chore(coordinator, "Do Homework", points=5, requires_approval=True)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    await coordinator.async_approve_chore(completion.id)

    after = coordinator.get_child(child.id)
    assert after.points == 30, "approval should release the 5 withheld points"
    assert coordinator.storage.get_pending_completions() == [], (
        "an approved completion must leave the approval queue"
    )


async def test_approving_the_same_completion_twice_pays_twice(
    coordinator: ChoremanderCoordinator,
) -> None:
    """CURRENT BEHAVIOUR (defect #2, see test plan §6.2) - approve is not idempotent.

    `async_approve_chore` never checks `completion.approved`, so every call
    re-runs `_award_points`. A parent who double-taps Approve pays the child
    twice against a single completion record. This test pins today's behaviour
    so that a future fix shows up as a deliberate change.
    """
    child = await _make_child(coordinator, "Krishna", points=25)
    chore = await _make_chore(coordinator, "Do Homework", points=5, requires_approval=True)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    await coordinator.async_approve_chore(completion.id)
    assert coordinator.get_child(child.id).points == 30, "first approval pays 5"

    await coordinator.async_approve_chore(completion.id)

    after = coordinator.get_child(child.id)
    assert after.points == 35, (
        "second approval pays another 5 - the guard that should make this a no-op does not exist"
    )
    assert after.total_points_earned == 10, "lifetime earnings are double-counted too"
    assert after.total_chores_completed == 2, (
        "the lifetime chore count is incremented per approval, not per completion"
    )
    assert len(coordinator.storage.get_completions()) == 1, (
        "there is still only one completion record backing the double payout"
    )


async def test_approving_uses_the_chore_points_at_approval_time(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The payout is read from the chore when approved, not when completed."""
    child = await _make_child(coordinator, "Krishna")
    chore = await _make_chore(coordinator, "Do Homework", points=5, requires_approval=True)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    repriced = coordinator.get_chore(chore.id)
    repriced.points = 20
    await coordinator.async_update_chore(repriced)

    await coordinator.async_approve_chore(completion.id)

    assert coordinator.get_child(child.id).points == 20, (
        "editing the chore between completion and approval changes what is paid"
    )
    stored = coordinator.storage.get_completions()[0]
    assert stored.points_awarded == 20, (
        "the completion record must reflect the amount actually paid, not the old price"
    )


async def test_approving_an_unknown_completion_id_is_a_silent_noop(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A stale Approve press must not raise or move money."""
    child = await _make_child(coordinator, "Krishna", points=25)

    with patch.object(
        coordinator.storage, "async_save", wraps=coordinator.storage.async_save
    ) as save:
        await coordinator.async_approve_chore("no_such_completion")

    assert save.call_count == 0, "nothing changed, so nothing should have been persisted"
    assert coordinator.get_child(child.id).points == 25, "the balance must be untouched"


async def test_approving_a_completion_whose_chore_was_deleted_pays_nothing(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The `if chore and child` guard makes the approval a no-op, not an error."""
    child = await _make_child(coordinator, "Krishna", points=25)
    chore = await _make_chore(coordinator, "Do Homework", points=5, requires_approval=True)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    await coordinator.async_remove_chore(chore.id)
    await coordinator.async_approve_chore(completion.id)

    assert coordinator.get_child(child.id).points == 25, (
        "a completion pointing at a deleted chore has no price, so nothing is paid"
    )
    assert len(coordinator.storage.get_pending_completions()) == 1, (
        "the orphaned completion is left stuck in the approval queue"
    )


# ---------------------------------------------------------------------------
# Rejecting a completion
# ---------------------------------------------------------------------------


async def test_rejecting_an_already_paid_completion_claws_the_points_back(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Undoing a no-approval chore deducts exactly what it paid."""
    child = await _make_child(coordinator, "Krishna", points=25)
    chore = await _make_chore(coordinator, "Make Bed", points=3)
    completion = await coordinator.async_complete_chore(chore.id, child.id)
    assert coordinator.get_child(child.id).points == 28, "precondition: the chore paid 3"

    await coordinator.async_reject_chore(completion.id)

    after = coordinator.get_child(child.id)
    assert after.points == 25, "rejecting must return the balance to where it started"
    assert coordinator.storage.get_completions() == [], (
        "a rejected completion record is deleted outright, not marked rejected"
    )
    assert after.total_points_earned == 3, (
        "CURRENT BEHAVIOUR: lifetime earnings are not rolled back on rejection"
    )
    assert after.total_chores_completed == 1, (
        "CURRENT BEHAVIOUR: the lifetime chore count is not rolled back either"
    )


async def test_rejecting_never_drives_a_balance_negative(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A clawback larger than the balance clamps at zero rather than going negative."""
    child = await _make_child(coordinator, "Krishna")
    chore = await _make_chore(coordinator, "Make Bed", points=3)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    # The child spends almost everything before the parent gets round to undoing it.
    broke = coordinator.get_child(child.id)
    broke.points = 1
    await coordinator.async_update_child(broke)

    await coordinator.async_reject_chore(completion.id)

    assert coordinator.get_child(child.id).points == 0, (
        "deducting 3 from a balance of 1 must clamp at 0, never -2"
    )


async def test_rejecting_a_still_pending_completion_deducts_nothing(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`points_awarded` is 0 while pending, so the deduction branch is skipped."""
    child = await _make_child(coordinator, "Krishna", points=25)
    chore = await _make_chore(coordinator, "Do Homework", points=5, requires_approval=True)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    await coordinator.async_reject_chore(completion.id)

    assert coordinator.get_child(child.id).points == 25, (
        "rejecting work that was never paid for must not take points away"
    )
    assert coordinator.storage.get_pending_completions() == [], (
        "the rejected completion must be cleared out of the approval queue"
    )


async def test_rejecting_an_unknown_completion_id_still_saves_and_refreshes(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The loop only `break`s; the removal, save and refresh run unconditionally."""
    child = await _make_child(coordinator, "Krishna", points=25)
    chore = await _make_chore(coordinator, "Make Bed", points=3)
    kept = await coordinator.async_complete_chore(chore.id, child.id)

    with patch.object(
        coordinator.storage, "async_save", wraps=coordinator.storage.async_save
    ) as save:
        await coordinator.async_reject_chore("no_such_completion")

    assert save.call_count == 1, (
        "unlike approve, reject saves even when the completion id matches nothing"
    )
    assert [c.id for c in coordinator.storage.get_completions()] == [kept.id], (
        "the unrelated completion must survive a rejection aimed at a missing id"
    )
    assert coordinator.get_child(child.id).points == 28, "no balance may move"
