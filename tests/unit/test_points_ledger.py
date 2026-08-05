"""The append-only points ledger.

Every code path that moves a child's balance writes exactly one
`PointsTransaction`, and the ledger has to *explain* the balance: the sum of a
child's entries must equal what they currently hold. That reconciliation
property (see `test_the_ledger_reconciles_with_the_balance_...` below) is the
whole point of the feature - a card that renders "why do I have 18 stars?" is
only trustworthy if the entries add up to 18.

Balances here are set with `_set_balance`, which writes the child record
directly and therefore deliberately writes *no* ledger entry. Tests that assert
reconciliation never use it; they start a child at zero and only move points
through the real coordinator APIs.

Rewards are created with `override_point_value=True` so their cost is the
stored number rather than a figure derived from whatever chores happen to
exist. Dynamic pricing is covered in `test_coordinator_pricing.py`; mixing it in
here would make the ledger arithmetic depend on it.
"""

from __future__ import annotations

from homeassistant.util import dt as dt_util

from custom_components.choremander.const import (
    EVENT_CHORE_APPROVED,
    EVENT_CHORE_REJECTED,
    EVENT_POINTS_ADDED,
    EVENT_POINTS_REMOVED,
    EVENT_REWARD_CLAIMED,
    EVENT_REWARD_REFUNDED,
    MAX_POINTS_TRANSACTIONS,
    MAX_REASON_LENGTH,
)
from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import PointsTransaction


def _ledger(
    coordinator: ChoremanderCoordinator, child_id: str | None = None
) -> list[PointsTransaction]:
    """The persisted ledger, oldest first, optionally for one child."""
    entries = coordinator.storage.get_points_transactions()
    if child_id is None:
        return entries
    return [t for t in entries if t.child_id == child_id]


async def _set_balance(coordinator: ChoremanderCoordinator, child_id: str, points: int) -> None:
    """Give a child an opening balance *without* recording it in the ledger.

    Used only by tests that assert on a single entry in isolation. Any test
    about reconciliation has to earn its points through the real APIs instead,
    or it would be asserting against a balance the ledger never saw.
    """
    child = coordinator.get_child(child_id)
    child.points = points
    await coordinator.async_update_child(child)


def _assert_reconciles(coordinator: ChoremanderCoordinator, child_id: str, step: str) -> None:
    """Assert the child's entries sum to the balance they are meant to explain."""
    balance = coordinator.get_child(child_id).points
    entries = _ledger(coordinator, child_id)
    total = sum(t.points for t in entries)
    assert total == balance, (
        f"after {step}: the ledger sums to {total} but the balance is {balance}; "
        f"entries were {[(t.event_type, t.points) for t in entries]}"
    )


# ---------------------------------------------------------------------------
# Chore paths
# ---------------------------------------------------------------------------


async def test_completing_a_chore_that_needs_no_approval_credits_the_ledger_once(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The credit happens at completion time, so that is when it is accounted for.

    A no-approval chore is awarded and approved in the same call. It must still
    produce one entry - not zero (the balance moved) and not two (completion and
    approval are the same event here).
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore(
        name="Feed the cat", points=8, requires_approval=False
    )

    completion = await coordinator.async_complete_chore(chore.id, child.id)

    entries = _ledger(coordinator)
    assert len(entries) == 1, "one balance movement must produce exactly one entry"
    entry = entries[0]
    assert entry.event_type == EVENT_CHORE_APPROVED, (
        "an auto-approved completion is a chore approval, not a bare bonus"
    )
    assert entry.points == 8, "the credit must equal the chore's value"
    assert entry.child_id == child.id, "the entry belongs to the child who did the work"
    assert entry.chore_id == chore.id, "the entry must name the chore that earned it"
    assert entry.related_id == completion.id, (
        "the entry must link back to the completion record it accounts for"
    )
    assert entry.reward_id == "", "no reward is involved in a chore credit"
    assert coordinator.get_child(child.id).points == 8, (
        "and the balance the entry explains must actually be 8"
    )


async def test_a_chore_awaiting_approval_writes_nothing_until_it_is_approved(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Nothing has been credited yet, so there is nothing to account for.

    Writing an entry at completion time would show points in the ledger that the
    child cannot spend, and would double-count once a parent approved.
    """
    child = await coordinator.async_add_child("Radha")
    chore = await coordinator.async_add_chore(name="Do homework", points=5, requires_approval=True)

    completion = await coordinator.async_complete_chore(chore.id, child.id)

    assert _ledger(coordinator) == [], (
        "a pending completion has credited nothing, so it must not appear in the ledger"
    )
    assert coordinator.get_child(child.id).points == 0, "precondition: nothing credited"

    await coordinator.async_approve_chore(completion.id)

    entries = _ledger(coordinator)
    assert len(entries) == 1, "approval is the first and only movement for this chore"
    entry = entries[0]
    assert (entry.event_type, entry.points) == (EVENT_CHORE_APPROVED, 5), (
        "approval credits the chore's full value"
    )
    assert (entry.child_id, entry.chore_id, entry.related_id) == (
        child.id,
        chore.id,
        completion.id,
    ), "the entry must identify the child, the chore and the completion it settles"
    assert coordinator.get_child(child.id).points == 5, "and the balance follows"


async def test_rejecting_an_approved_chore_claws_the_points_back_as_its_own_entry(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A reversal is a second entry, not an erasure of the first.

    The ledger is append-only: history has to keep showing that the points were
    awarded and then taken back, otherwise a parent cannot explain the swing.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore(
        name="Take out the bins", points=12, requires_approval=False
    )
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    await coordinator.async_reject_chore(completion.id)

    entries = _ledger(coordinator)
    assert [(t.event_type, t.points) for t in entries] == [
        (EVENT_CHORE_APPROVED, 12),
        (EVENT_CHORE_REJECTED, -12),
    ], "the award must survive the reversal; both movements stay on the record"
    reversal = entries[-1]
    assert reversal.child_id == child.id, "the clawback hits the child who was credited"
    assert reversal.chore_id == chore.id, "and names the chore that is being undone"
    assert reversal.related_id == completion.id, (
        "the reversal must point at the completion it reverses, even though that "
        "completion is deleted afterwards"
    )
    assert coordinator.get_child(child.id).points == 0, "the balance returns to zero"


# ---------------------------------------------------------------------------
# Reward paths
# ---------------------------------------------------------------------------


async def test_claiming_a_reward_debits_the_ledger_and_links_to_the_claim(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Points leave the balance at claim time, so the debit is recorded there."""
    child = await coordinator.async_add_child("Krishna")
    await _set_balance(coordinator, child.id, 50)
    reward = await coordinator.async_add_reward(
        name="Movie night", cost=30, override_point_value=True
    )

    claim = await coordinator.async_claim_reward(reward.id, child.id)

    entries = _ledger(coordinator)
    assert len(entries) == 1, "one claim, one entry"
    entry = entries[0]
    assert entry.event_type == EVENT_REWARD_CLAIMED, "spending is a claim, not a penalty"
    assert entry.points == -30, (
        "a debit is stored as a negative number so entries can simply be summed"
    )
    assert entry.child_id == child.id, "the entry belongs to the child who paid"
    assert entry.reward_id == reward.id, "and must name the reward that was bought"
    assert entry.related_id == claim.id, "and link back to the claim record"
    assert entry.chore_id == "", "no chore is involved in a reward claim"
    assert coordinator.get_child(child.id).points == 20, "50 - 30"


async def test_approving_a_reward_claim_never_charges_the_child_a_second_time(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The child already paid when they claimed; approval only flips a flag.

    An entry here would both double-charge in the ledger's arithmetic and
    disagree with a balance that did not move.
    """
    child = await coordinator.async_add_child("Radha")
    await _set_balance(coordinator, child.id, 50)
    reward = await coordinator.async_add_reward(
        name="Ice cream", cost=15, override_point_value=True
    )
    claim = await coordinator.async_claim_reward(reward.id, child.id)
    assert len(_ledger(coordinator)) == 1, "precondition: the claim wrote one debit"

    await coordinator.async_approve_reward(claim.id)

    assert [(t.event_type, t.points) for t in _ledger(coordinator)] == [
        (EVENT_REWARD_CLAIMED, -15)
    ], "approving a claim moves no points, so it must not write an entry"
    assert coordinator.get_child(child.id).points == 35, "the balance is untouched by approval"


async def test_rejecting_a_reward_claim_refunds_with_a_single_credit(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A refused claim gives the points back, and the ledger says so explicitly."""
    child = await coordinator.async_add_child("Krishna")
    await _set_balance(coordinator, child.id, 50)
    reward = await coordinator.async_add_reward(
        name="Late bedtime", cost=20, override_point_value=True
    )
    claim = await coordinator.async_claim_reward(reward.id, child.id)

    await coordinator.async_reject_reward(claim.id)

    entries = _ledger(coordinator)
    assert [(t.event_type, t.points) for t in entries] == [
        (EVENT_REWARD_CLAIMED, -20),
        (EVENT_REWARD_REFUNDED, 20),
    ], "the debit stays on the record and the refund is added as its own credit"
    refund = entries[-1]
    assert refund.child_id == child.id, "the refund goes to the child who paid"
    assert refund.reward_id == reward.id, "and names the reward that was refused"
    assert refund.related_id == claim.id, (
        "and links back to the claim, so a card can pair the debit with its refund"
    )
    assert coordinator.get_child(child.id).points == 50, "the balance is made whole"


# ---------------------------------------------------------------------------
# Manual adjustments
# ---------------------------------------------------------------------------


async def test_a_manual_bonus_and_penalty_each_record_one_signed_entry(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Manual adjustments are the least self-explanatory movements in the system.

    They have no chore or reward behind them, so the free-text reason a parent
    typed is the only explanation there will ever be - it has to be kept, and
    kept apart from the machine-readable `event_type`.
    """
    child = await coordinator.async_add_child("Radha")

    await coordinator.async_add_points(child.id, 9, reason="Helped a sibling")
    await coordinator.async_remove_points(child.id, 4, reason="Backchat")

    entries = _ledger(coordinator)
    assert [(t.event_type, t.points, t.reason) for t in entries] == [
        (EVENT_POINTS_ADDED, 9, "Helped a sibling"),
        (EVENT_POINTS_REMOVED, -4, "Backchat"),
    ], "a penalty is stored negative so the ledger can be summed without branching"
    assert all(t.child_id == child.id for t in entries), "both belong to the same child"
    assert all(t.chore_id == "" and t.reward_id == "" for t in entries), (
        "a manual adjustment references no chore or reward"
    )
    assert coordinator.get_child(child.id).points == 5, "0 + 9 - 4"


async def test_adjustments_that_move_nothing_leave_no_trace_in_the_ledger(
    coordinator: ChoremanderCoordinator,
) -> None:
    """An entry of zero explains nothing and would only be noise in the history.

    The third case is the interesting one: removing points from a child who has
    none is a *requested* movement of 10, but an *actual* movement of 0. It is
    the actual movement that decides whether anything is recorded.
    """
    child = await coordinator.async_add_child("Krishna")

    await coordinator.async_add_points(child.id, 0, reason="just checking")
    await coordinator.async_remove_points(child.id, 0, reason="also nothing")
    await coordinator.async_remove_points(child.id, 10, reason="already at zero")

    assert _ledger(coordinator) == [], (
        "none of these changed a balance, so none may appear in the ledger"
    )
    assert coordinator.get_child(child.id).points == 0, "the balance never moved"


# ---------------------------------------------------------------------------
# Clamping: the ledger has to agree with the balance it explains
# ---------------------------------------------------------------------------


async def test_a_penalty_larger_than_the_balance_records_only_what_was_taken(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Balances floor at zero, so a 50-point penalty on 20 points removes 20.

    Recording -50 would be recording a debt that was never collected: the ledger
    would sum to -30 while the child plainly holds 0.
    """
    child = await coordinator.async_add_child("Krishna")
    await _set_balance(coordinator, child.id, 20)

    await coordinator.async_remove_points(child.id, 50, reason="broke a window")

    entries = _ledger(coordinator)
    assert len(entries) == 1, "one penalty, one entry"
    assert entries[0].points == -20, (
        "the entry must record the 20 that were actually taken, not the 50 asked for"
    )
    assert entries[0].event_type == EVENT_POINTS_REMOVED, "still a penalty"
    assert coordinator.get_child(child.id).points == 0, "the balance floors at zero"


async def test_clawing_back_a_spent_chore_records_only_the_points_still_there(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The same clamp applies when a parent reverses an approval after the fact.

    The child earned 20, spent 15 on a reward, and then the chore is rejected.
    Only 5 points remain to take, so the clawback is -5 - the ledger stays in
    step with a balance that cannot go negative.
    """
    child = await coordinator.async_add_child("Radha")
    chore = await coordinator.async_add_chore(
        name="Mow the lawn", points=20, requires_approval=False
    )
    reward = await coordinator.async_add_reward(
        name="Comic book", cost=15, override_point_value=True
    )
    completion = await coordinator.async_complete_chore(chore.id, child.id)
    await coordinator.async_claim_reward(reward.id, child.id)
    assert coordinator.get_child(child.id).points == 5, "precondition: 20 earned, 15 spent"

    await coordinator.async_reject_chore(completion.id)

    entries = _ledger(coordinator)
    assert [(t.event_type, t.points) for t in entries] == [
        (EVENT_CHORE_APPROVED, 20),
        (EVENT_REWARD_CLAIMED, -15),
        (EVENT_CHORE_REJECTED, -5),
    ], "the clawback takes the 5 that were left, not the 20 originally awarded"
    assert coordinator.get_child(child.id).points == 0, "the balance floors at zero"
    _assert_reconciles(coordinator, child.id, "a clamped clawback")


# ---------------------------------------------------------------------------
# The reconciliation property
# ---------------------------------------------------------------------------


async def test_the_ledger_reconciles_with_the_balance_across_a_mixed_sequence(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The load-bearing property: entries sum to the balance, after every step.

    A newly added child starts at zero, so the ledger owes a complete account of
    everything that follows. This walks a household's realistic day - work
    pending then approved, an instant chore, a bonus, a purchase, a penalty, a
    reversal that clamps, a refund - and checks the invariant after each move, so
    a break points at the operation that caused it rather than at the end state.
    """
    child = await coordinator.async_add_child("Krishna")
    assert coordinator.get_child(child.id).points == 0, (
        "precondition: a new child starts at zero, so the ledger must explain everything"
    )

    dishes = await coordinator.async_add_chore(
        name="Wash the dishes", points=10, requires_approval=True, daily_limit=2
    )
    bins = await coordinator.async_add_chore(
        name="Take out the bins", points=6, requires_approval=False, daily_limit=2
    )
    big_reward = await coordinator.async_add_reward(
        name="Theme park", cost=25, override_point_value=True
    )
    small_reward = await coordinator.async_add_reward(
        name="Extra screen time", cost=8, override_point_value=True
    )

    first_dishes = await coordinator.async_complete_chore(dishes.id, child.id)
    second_dishes = await coordinator.async_complete_chore(dishes.id, child.id)
    _assert_reconciles(coordinator, child.id, "two dish completions awaiting approval")

    await coordinator.async_approve_chore(first_dishes.id)
    _assert_reconciles(coordinator, child.id, "approving the first dish completion")

    await coordinator.async_approve_chore(second_dishes.id)
    _assert_reconciles(coordinator, child.id, "approving the second dish completion")

    bins_completion = await coordinator.async_complete_chore(bins.id, child.id)
    _assert_reconciles(coordinator, child.id, "an instantly-credited chore")

    await coordinator.async_add_points(child.id, 5, reason="Tidied up unasked")
    _assert_reconciles(coordinator, child.id, "a manual bonus")
    assert coordinator.get_child(child.id).points == 31, "10 + 10 + 6 + 5"

    await coordinator.async_claim_reward(big_reward.id, child.id)
    _assert_reconciles(coordinator, child.id, "claiming an expensive reward")

    await coordinator.async_remove_points(child.id, 4, reason="Backchat")
    _assert_reconciles(coordinator, child.id, "a manual penalty")
    assert coordinator.get_child(child.id).points == 2, "31 - 25 - 4"

    # Worth 6, but only 2 remain - the clawback has to clamp.
    await coordinator.async_reject_chore(bins_completion.id)
    _assert_reconciles(coordinator, child.id, "a clawback bigger than the balance")
    assert coordinator.get_child(child.id).points == 0, "the clamp floors the balance"

    await coordinator.async_add_points(child.id, 12, reason="Weekly allowance")
    redone_bins = await coordinator.async_complete_chore(bins.id, child.id)
    _assert_reconciles(coordinator, child.id, "an allowance and a redone chore")

    small_claim = await coordinator.async_claim_reward(small_reward.id, child.id)
    _assert_reconciles(coordinator, child.id, "claiming a cheap reward")

    await coordinator.async_reject_reward(small_claim.id)
    _assert_reconciles(coordinator, child.id, "the cheap claim being refused")

    await coordinator.async_add_points(child.id, 0, reason="no-op")
    _assert_reconciles(coordinator, child.id, "a zero-point no-op")

    assert [(t.event_type, t.points) for t in _ledger(coordinator, child.id)] == [
        (EVENT_CHORE_APPROVED, 10),
        (EVENT_CHORE_APPROVED, 10),
        (EVENT_CHORE_APPROVED, 6),
        (EVENT_POINTS_ADDED, 5),
        (EVENT_REWARD_CLAIMED, -25),
        (EVENT_POINTS_REMOVED, -4),
        (EVENT_CHORE_REJECTED, -2),
        (EVENT_POINTS_ADDED, 12),
        (EVENT_CHORE_APPROVED, 6),
        (EVENT_REWARD_CLAIMED, -8),
        (EVENT_REWARD_REFUNDED, 8),
    ], "the full history of the day, in the order it happened"
    assert coordinator.get_child(child.id).points == 18, (
        "0 + 10 + 10 + 6 + 5 - 25 - 4 - 2 + 12 + 6 - 8 + 8"
    )
    assert {t.related_id for t in _ledger(coordinator, child.id)} >= {
        first_dishes.id,
        second_dishes.id,
        bins_completion.id,
        redone_bins.id,
        small_claim.id,
    }, "every record that moved points is reachable from the ledger"


async def test_one_childs_activity_never_lands_in_another_childs_ledger(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Two children share every chore and reward, but not a single entry.

    Reconciliation is per-child, so a misattributed `child_id` would make one
    balance unexplainable and inflate the other - the kind of bug that only
    shows up once a second child exists.
    """
    krishna = await coordinator.async_add_child("Krishna")
    radha = await coordinator.async_add_child("Radha")
    chore = await coordinator.async_add_chore(
        name="Set the table", points=7, requires_approval=False
    )
    reward = await coordinator.async_add_reward(name="Sticker", cost=5, override_point_value=True)

    await coordinator.async_complete_chore(chore.id, krishna.id)
    await coordinator.async_complete_chore(chore.id, radha.id)
    await coordinator.async_claim_reward(reward.id, radha.id)
    await coordinator.async_add_points(krishna.id, 3, reason="Kind to the dog")

    assert [(t.event_type, t.points) for t in _ledger(coordinator, krishna.id)] == [
        (EVENT_CHORE_APPROVED, 7),
        (EVENT_POINTS_ADDED, 3),
    ], "Krishna's ledger holds his chore and his bonus, and nothing of Radha's"
    assert [(t.event_type, t.points) for t in _ledger(coordinator, radha.id)] == [
        (EVENT_CHORE_APPROVED, 7),
        (EVENT_REWARD_CLAIMED, -5),
    ], "Radha's ledger holds her chore and her purchase, and nothing of Krishna's"

    _assert_reconciles(coordinator, krishna.id, "shared chores and rewards")
    _assert_reconciles(coordinator, radha.id, "shared chores and rewards")
    assert len(_ledger(coordinator)) == 4, (
        "the combined ledger is exactly the two children's entries - nothing stray"
    )


# ---------------------------------------------------------------------------
# The rolling cap
# ---------------------------------------------------------------------------


def _numbered_transaction(index: int, child_id: str = "child_a") -> PointsTransaction:
    """A transaction whose id and reason encode the order it was appended in."""
    return PointsTransaction(
        child_id=child_id,
        points=1,
        event_type=EVENT_POINTS_ADDED,
        created_at=dt_util.now(),
        reason=f"entry {index}",
        id=f"txn{index:05d}",
    )


async def test_the_ledger_drops_its_oldest_entries_once_it_passes_its_ceiling(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The ledger is a rolling window, and it is the *recent* history that matters.

    A household that runs for years would otherwise grow an unbounded blob in
    `.storage`. Dropping from the wrong end would leave the newest activity -
    the only part anyone looks at - missing.
    """
    overflow = 13
    total = MAX_POINTS_TRANSACTIONS + overflow
    for index in range(total):
        coordinator.storage.add_points_transaction(_numbered_transaction(index))

    # Read the raw stored list, not `get_points_transactions()`: the cap must
    # already have been applied by the appends themselves.
    raw = coordinator.storage._data["points_transactions"]
    assert len(raw) == MAX_POINTS_TRANSACTIONS, (
        f"the stored list must be trimmed on append; {total} appends left {len(raw)} rows"
    )
    assert raw[0]["id"] == f"txn{overflow:05d}", (
        f"the first {overflow} entries are the oldest and must be the ones dropped"
    )
    assert raw[-1]["id"] == f"txn{total - 1:05d}", "the newest entry must survive"
    assert not any(row["reason"] == "entry 0" for row in raw), (
        "the very first entry must be gone from storage, not merely hidden on read"
    )

    entries = coordinator.storage.get_points_transactions()
    assert len(entries) == MAX_POINTS_TRANSACTIONS, "and the read agrees with the store"
    assert [t.id for t in entries] == [row["id"] for row in raw], (
        "reading must not reorder or re-trim - it just hydrates what is stored"
    )


async def test_the_cap_bites_exactly_once_the_ceiling_is_exceeded(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Off-by-one at the boundary would silently drop a valid entry (or keep one too many)."""
    for index in range(MAX_POINTS_TRANSACTIONS):
        coordinator.storage.add_points_transaction(_numbered_transaction(index))

    raw = coordinator.storage._data["points_transactions"]
    assert len(raw) == MAX_POINTS_TRANSACTIONS, "a full ledger must not be trimmed"
    assert raw[0]["id"] == "txn00000", "nothing is dropped until the ceiling is passed"

    coordinator.storage.add_points_transaction(_numbered_transaction(MAX_POINTS_TRANSACTIONS))

    raw = coordinator.storage._data["points_transactions"]
    assert len(raw) == MAX_POINTS_TRANSACTIONS, "one over the ceiling stays at the ceiling"
    assert raw[0]["id"] == "txn00001", "and the single oldest entry is the one dropped"


# ---------------------------------------------------------------------------
# Ordering, shape and round-tripping
# ---------------------------------------------------------------------------


async def test_entries_are_oldest_first_with_aware_timestamps_and_unique_ids(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A ledger is read as a story, so its order and its clock have to be sound.

    Naive timestamps would render at the wrong hour for anyone not on UTC, and
    duplicate ids would break any card that keys rows by id.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore(
        name="Water the plants", points=4, requires_approval=False
    )
    await coordinator.async_complete_chore(chore.id, child.id)
    await coordinator.async_add_points(child.id, 6, reason="Helped a sibling")
    await coordinator.async_remove_points(child.id, 2, reason="Backchat")

    entries = _ledger(coordinator)
    assert [t.event_type for t in entries] == [
        EVENT_CHORE_APPROVED,
        EVENT_POINTS_ADDED,
        EVENT_POINTS_REMOVED,
    ], "entries come back in the order they happened, oldest first"

    stamps = [t.created_at for t in entries]
    assert all(s.tzinfo is not None for s in stamps), (
        "every timestamp must be timezone-aware or it cannot be rendered locally"
    )
    assert stamps == sorted(stamps), "timestamps must not run backwards through the list"
    assert len({t.id for t in entries}) == len(entries), "ids must be unique"


async def test_a_transaction_round_trips_losslessly_through_its_dict_form(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The ledger only ever exists as JSON on disk, so serialisation must be exact.

    A field silently dropped by `to_dict()` would not break anything until a
    restart, at which point the history would quietly change.
    """
    original = PointsTransaction(
        child_id="child_krishna",
        points=-17,
        event_type=EVENT_REWARD_CLAIMED,
        created_at=dt_util.now(),
        reason="Traded for screen time",
        chore_id="chore_dishes",
        reward_id="reward_screen",
        related_id="claim_abc",
        id="txn_fixed",
    )

    assert PointsTransaction.from_dict(original.to_dict()) == original, (
        "every field, including the signed points and the three link ids, must survive"
    )

    child = await coordinator.async_add_child("Radha")
    await coordinator.async_add_points(child.id, 5, reason="Helped a sibling")
    stored = _ledger(coordinator)[0]
    assert PointsTransaction.from_dict(stored.to_dict()) == stored, (
        "and an entry the coordinator actually wrote round-trips just the same"
    )


async def test_a_repriced_refund_records_what_moved_not_what_was_originally_paid(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The ledger follows the balance even when the balance is wrong.

    `async_reject_reward` re-derives the price at rejection time rather than
    refunding what was charged, so editing a chore in between changes the refund
    (see `test_rejecting_a_claim_reprices_it_and_can_destroy_points` in
    `test_coordinator_rewards.py`). Whatever one thinks of that, the entry must
    record the movement that actually happened - a ledger that reported the
    original 20 while the balance rose by 30 would explain nothing.
    """
    child = await coordinator.async_add_child("Krishna")
    await _set_balance(coordinator, child.id, 60)
    await coordinator.async_add_chore(name="Wash the dishes", points=10)
    reward = await coordinator.async_add_reward(
        name="Theme park", cost=999, days_to_goal=2, override_point_value=False
    )

    claim = await coordinator.async_claim_reward(reward.id, child.id)
    assert _ledger(coordinator)[-1].points == -20, (
        "precondition: 10 points/day over a 2-day goal charges 20"
    )
    charged_down_to = coordinator.get_child(child.id).points
    assert charged_down_to == 40, "precondition: 60 - 20"

    # A second chore raises the child's daily earnings, and with them the price.
    await coordinator.async_add_chore(name="Feed the cat", points=5)
    await coordinator.async_reject_reward(claim.id)

    refund = _ledger(coordinator)[-1]
    restored_to = coordinator.get_child(child.id).points
    assert refund.event_type == EVENT_REWARD_REFUNDED, "the reversal is a refund"
    assert refund.points == restored_to - charged_down_to, (
        f"the entry must record the {restored_to - charged_down_to} the balance "
        f"actually moved by, but recorded {refund.points}"
    )
    assert refund.points == 30, (
        "the repricing is real - 15 points/day over 2 days - and the ledger says so "
        "rather than quietly echoing the 20 that was paid"
    )


# ---------------------------------------------------------------------------
# Payload discipline and edge amounts
# ---------------------------------------------------------------------------


async def test_a_very_long_reason_is_truncated_before_it_is_stored(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`reason` is free text and unbounded at the service boundary.

    The activity feed shares one entity's 16KiB attribute budget. Going over it
    makes Home Assistant stop recording the entity entirely, with no exception
    and no log line, so one automation passing an essay would silently take the
    feed's history out.
    """
    child = await coordinator.async_add_child("Krishna")
    essay = "why she deserves this " * 50

    await coordinator.async_add_points(child.id, 5, reason=essay)

    entry = coordinator.storage.get_points_transactions()[0]
    assert len(entry.reason) == MAX_REASON_LENGTH
    assert entry.reason == essay[:MAX_REASON_LENGTH]
    assert entry.points == 5, "truncating the note must not disturb the amount"


async def test_a_reason_shorter_than_the_cap_is_stored_verbatim(
    coordinator: ChoremanderCoordinator,
) -> None:
    child = await coordinator.async_add_child("Radha")
    await coordinator.async_add_points(child.id, 5, reason="Helped a sibling")

    assert coordinator.storage.get_points_transactions()[0].reason == "Helped a sibling"


async def test_a_zero_point_chore_still_appears_in_the_ledger(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The feed answers "I did this, where is it?", so the chore must not vanish.

    A zero-point chore moves no balance, but it is still something that
    happened. Suppressing it would show the chore as pending and then have it
    disappear on approval, which reads as the approval having failed.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore("Say Goodnight", points=0, requires_approval=False)

    completion = await coordinator.async_complete_chore(chore.id, child.id)

    ledger = coordinator.storage.get_points_transactions()
    assert len(ledger) == 1
    assert ledger[0].event_type == "chore_approved"
    assert ledger[0].points == 0
    assert ledger[0].related_id == completion.id


async def test_a_zero_point_adjustment_still_leaves_no_trace(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A manual adjustment of nothing is not an event, unlike a chore."""
    child = await coordinator.async_add_child("Radha")

    await coordinator.async_add_points(child.id, 0, reason="never mind")
    await coordinator.async_remove_points(child.id, 0, reason="never mind")

    assert coordinator.storage.get_points_transactions() == []


# ---------------------------------------------------------------------------
# Streaks and approval latency
# ---------------------------------------------------------------------------


async def test_a_streak_does_not_wait_on_a_parent(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Doing the work starts the streak; approving it is the parent's business.

    Counting only approved completions made the streak a measure of parental
    responsiveness. A child who did their chores every day but whose parent
    approved at the weekend would watch it read zero for days and then snap
    back, which is both wrong and impossible to explain to them.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore("Do Homework", points=5, requires_approval=True)

    await coordinator.async_complete_chore(chore.id, child.id)

    assert coordinator.get_child(child.id).current_streak == 1, (
        "the work is done, so today counts even though nobody has approved it"
    )
    assert coordinator.storage.get_points_transactions() == [], (
        "and yet no points have moved, so the ledger stays empty"
    )


async def test_rejecting_the_work_takes_the_day_back(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The self-correction that makes counting pending work safe.

    Rejecting deletes the completion, so the day simply stops being active on
    the next read. Nothing has to remember to undo anything - which is the whole
    argument for deriving these figures rather than maintaining a counter.
    """
    child = await coordinator.async_add_child("Radha")
    chore = await coordinator.async_add_chore("Practice Piano", points=4, requires_approval=True)

    completion = await coordinator.async_complete_chore(chore.id, child.id)
    assert coordinator.get_child(child.id).current_streak == 1

    await coordinator.async_reject_chore(completion.id)

    after = coordinator.get_child(child.id)
    assert after.current_streak == 0, "the only work that day was rejected"
    assert after.best_streak == 0, "and it was never a best streak either"


async def test_several_chores_in_one_day_are_still_one_day(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The case an incrementing counter gets wrong."""
    child = await coordinator.async_add_child("Krishna")
    for name in ("Make Bed", "Dishes", "Homework"):
        chore = await coordinator.async_add_chore(name, points=2, requires_approval=False)
        await coordinator.async_complete_chore(chore.id, child.id)

    after = coordinator.get_child(child.id)
    assert after.current_streak == 1, "three chores, one day, one day of streak"
    assert after.total_chores_completed == 3, "but three chores really were done"


async def test_rejecting_a_zero_point_chore_still_undoes_it(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Approval, not the amount, is what has to be rolled back.

    Approving a chore increments the lifetime chore count even when the chore
    is worth nothing. Keying the rollback on `points_awarded > 0` left that
    count permanently inflated - and it now unlocks streak-card milestones, so
    a child could earn "10 done" on work that was rejected.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore("Say Goodnight", points=0, requires_approval=False)
    completion = await coordinator.async_complete_chore(chore.id, child.id)
    assert coordinator.get_child(child.id).total_chores_completed == 1

    await coordinator.async_reject_chore(completion.id)

    after = coordinator.get_child(child.id)
    assert after.total_chores_completed == 0, "the chore was undone, so it is not done"
    assert after.points == 0

    events = [t.event_type for t in coordinator.storage.get_points_transactions()]
    assert events == ["chore_approved", "chore_rejected"], (
        "and the feed says it was undone, rather than leaving the approval row "
        f"standing alone describing work that no longer exists; got {events}"
    )


async def test_rejecting_work_that_was_never_approved_changes_nothing(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Nothing was credited, so there is nothing to claw back."""
    child = await coordinator.async_add_child("Radha")
    chore = await coordinator.async_add_chore("Do Homework", points=5, requires_approval=True)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    await coordinator.async_reject_chore(completion.id)

    after = coordinator.get_child(child.id)
    assert after.points == 0
    assert after.total_chores_completed == 0
    assert after.total_points_earned == 0
    assert coordinator.storage.get_points_transactions() == [], (
        "no balance ever moved, so the ledger has nothing to say"
    )
