"""Tests for `activity.build_activity_feed`.

The activity card renders whatever this function returns, in the order it
returns it, and reads every row by attribute *key*. Three properties therefore
matter more than anything else here and each is pinned below:

* **Order.** The feed is the card's timeline. It must be strictly newest-first
  by *instant*, not by the text of a formatted timestamp - a local-time string
  sorts wrongly across a daylight-saving change, when the UTC offset inside it
  shifts.
* **Omission.** Optional keys are dropped when they have nothing to say. Home
  Assistant's recorder refuses to store a state whose attributes exceed 16KiB,
  and every `"reason": ""` we do not send is budget the feed keeps. Absence is
  the contract, so these tests assert `not in`, never `== ""`.
* **Survival.** A chore or reward can be deleted while the ledger entry that
  records it lives on. The entry is still true and must still appear, with the
  name key simply absent so the card falls back to its generic wording.

The function takes no Home Assistant objects, so everything here is a plain
synchronous test over hand-built models.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from custom_components.choremander.activity import build_activity_feed
from custom_components.choremander.const import (
    EVENT_CHORE_APPROVED,
    EVENT_CHORE_PENDING,
    EVENT_CHORE_REJECTED,
    EVENT_POINTS_ADDED,
    EVENT_POINTS_REMOVED,
    EVENT_REWARD_CLAIMED,
)
from custom_components.choremander.models import (
    Child,
    Chore,
    ChoreCompletion,
    PointsTransaction,
    Reward,
    format_datetime,
    parse_datetime,
)

# A real daylight-saving-observing zone. Fixed-offset zones cannot express the
# bug these tests exist to catch.
PACIFIC = ZoneInfo("America/Los_Angeles")

# In 2026 US daylight saving starts 08 March (02:00 PST -> 03:00 PDT) and ends
# 01 November (02:00 PDT -> 01:00 PST).
FALL_BACK_DAY = (2026, 11, 1)
SPRING_FORWARD_DAY = (2026, 3, 8)

BASE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

KRISHNA = Child(name="Krishna", id="child_krishna")
RADHA = Child(name="Radha", id="child_radha")
CHILDREN = [KRISHNA, RADHA]

HOMEWORK = Chore(name="Do Homework", points=5, id="chore_homework")
DISHES = Chore(name="Clear the Dishes", points=3, id="chore_dishes")
FREEBIE = Chore(name="Say Thank You", points=0, id="chore_freebie")
CHORES = [HOMEWORK, DISHES, FREEBIE]

SCREEN_TIME = Reward(name="30 Min Screen Time", cost=10, icon="mdi:television", id="reward_screen")
REWARDS = [SCREEN_TIME]


def _at(minutes: int) -> datetime:
    """A timestamp `minutes` after the fixed base instant."""
    return BASE + timedelta(minutes=minutes)


def _txn(**overrides: Any) -> PointsTransaction:
    """A ledger entry with sensible defaults, overridable per test."""
    fields: dict[str, Any] = {
        "child_id": "child_krishna",
        "points": 5,
        "event_type": EVENT_CHORE_APPROVED,
        "created_at": BASE,
        "reason": "",
        "chore_id": "",
        "reward_id": "",
        "related_id": "",
        "id": "txn_1",
    }
    fields.update(overrides)
    return PointsTransaction(**fields)


def _pending(**overrides: Any) -> ChoreCompletion:
    """An unapproved chore completion with sensible defaults."""
    fields: dict[str, Any] = {
        "chore_id": "chore_homework",
        "child_id": "child_krishna",
        "completed_at": BASE,
        "approved": False,
        "approved_at": None,
        "points_awarded": 0,
        "id": "comp_1",
    }
    fields.update(overrides)
    return ChoreCompletion(**fields)


def _feed(
    transactions: list[PointsTransaction] | None = None,
    pending_completions: list[ChoreCompletion] | None = None,
    *,
    children: list[Child] | None = None,
    chores: list[Chore] | None = None,
    rewards: list[Reward] | None = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    """Call `build_activity_feed` with the standard household as the default."""
    return build_activity_feed(
        transactions=transactions if transactions is not None else [],
        pending_completions=(pending_completions if pending_completions is not None else []),
        children=children if children is not None else CHILDREN,
        chores=chores if chores is not None else CHORES,
        rewards=rewards if rewards is not None else REWARDS,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_events_come_back_strictly_newest_first_whatever_order_they_arrived_in() -> None:
    """The feed is a timeline; input order is an implementation detail of storage."""
    transactions = [
        _txn(id="txn_middle", created_at=_at(20)),
        _txn(id="txn_oldest", created_at=_at(0)),
        _txn(id="txn_newest", created_at=_at(40)),
    ]

    events = _feed(transactions)

    assert [e["id"] for e in events] == ["txn_newest", "txn_middle", "txn_oldest"], (
        "the ledger is stored oldest-first and may be handed over unsorted; the feed "
        "must impose newest-first itself rather than trusting the input order"
    )
    stamps = [e["occurred_at"] for e in events]
    assert stamps == sorted(stamps, reverse=True), (
        "the published timestamps must descend too, or the card's own re-sorting "
        f"would disagree with the order it was given: {stamps}"
    )


def test_transactions_and_pending_completions_interleave_by_time() -> None:
    """Two separate sources, one merged stream - the card must not have to zip them."""
    transactions = [
        _txn(id="txn_early", created_at=_at(10)),
        _txn(id="txn_late", created_at=_at(30)),
    ]
    pending_completions = [
        _pending(id="comp_earliest", completed_at=_at(0)),
        _pending(id="comp_between", completed_at=_at(20)),
        _pending(id="comp_latest", completed_at=_at(40)),
    ]

    events = _feed(transactions, pending_completions)

    assert [e["id"] for e in events] == [
        "comp_latest",
        "txn_late",
        "comp_between",
        "txn_early",
        "comp_earliest",
    ], (
        "pending completions are activity too and must be sorted into the ledger "
        "stream by time, not appended after it"
    )


# ---------------------------------------------------------------------------
# The limit
# ---------------------------------------------------------------------------


def test_the_limit_keeps_the_newest_events_not_the_first_ones_encountered() -> None:
    """Truncating before sorting would publish the oldest history and hide today."""
    transactions = [_txn(id=f"txn_{index}", created_at=_at(index * 10)) for index in range(6)]
    # Hand them over oldest-first, exactly as storage does.
    events = _feed(transactions, limit=2)

    assert [e["id"] for e in events] == ["txn_5", "txn_4"], (
        "with a limit of 2 the feed must carry the two most recent events; slicing "
        "the input before sorting would have returned txn_0 and txn_1 instead"
    )


def test_the_limit_counts_across_both_sources_together() -> None:
    """The cap is on the merged feed, not per source."""
    transactions = [_txn(id="txn_old", created_at=_at(0))]
    pending_completions = [
        _pending(id="comp_new", completed_at=_at(30)),
        _pending(id="comp_older", completed_at=_at(10)),
    ]

    events = _feed(transactions, pending_completions, limit=2)

    assert [e["id"] for e in events] == ["comp_new", "comp_older"], (
        "a limit of 2 must yield 2 rows in total; the older ledger entry loses out "
        "to two newer pending completions rather than being guaranteed a slot"
    )


def test_a_limit_larger_than_the_history_returns_everything_available() -> None:
    """Asking for more than exists is not an error and must not pad the list."""
    transactions = [_txn(id=f"txn_{index}", created_at=_at(index)) for index in range(3)]

    events = _feed(transactions, limit=40)

    assert len(events) == 3, (
        "the limit is a ceiling, not a target; a quiet household publishes a short feed"
    )


@pytest.mark.parametrize("limit", [0, -1, -40])
def test_a_zero_or_negative_limit_publishes_nothing_rather_than_everything(
    limit: int,
) -> None:
    """A misconfigured cap must fail closed.

    Python's slicing would turn a negative limit into "all but the last N", so a
    bare `events[:limit]` would silently publish almost the whole history for what
    was meant to be a request for none of it.
    """
    transactions = [_txn(id=f"txn_{index}", created_at=_at(index)) for index in range(5)]
    pending_completions = [_pending(id="comp_1", completed_at=_at(9))]

    events = _feed(transactions, pending_completions, limit=limit)

    assert events == [], (
        f"a limit of {limit} must yield an empty feed, not raise and not fall back to "
        f"the full history; got {len(events)} events"
    )


# ---------------------------------------------------------------------------
# Name resolution
# ---------------------------------------------------------------------------


def test_child_chore_and_reward_names_are_resolved_from_the_passed_collections() -> None:
    """The feed carries display names so the card never has to cross-reference ids."""
    transactions = [
        _txn(
            id="txn_chore",
            created_at=_at(10),
            child_id="child_krishna",
            chore_id="chore_homework",
            points=5,
        ),
        _txn(
            id="txn_reward",
            created_at=_at(20),
            child_id="child_radha",
            event_type=EVENT_REWARD_CLAIMED,
            reward_id="reward_screen",
            points=-10,
        ),
    ]

    by_id = {e["id"]: e for e in _feed(transactions)}

    assert by_id["txn_chore"]["child_name"] == "Krishna"
    assert by_id["txn_chore"]["chore_name"] == "Do Homework"
    assert by_id["txn_reward"]["child_name"] == "Radha"
    assert by_id["txn_reward"]["reward_name"] == "30 Min Screen Time"
    assert by_id["txn_reward"]["reward_icon"] == "mdi:television", (
        "the reward's icon travels with its name so the card can draw the row "
        "without looking the reward up again"
    )
    assert by_id["txn_reward"]["points"] == -10, (
        "points are signed; a claim debits the child and must be published negative"
    )


def test_a_ledger_entry_outlives_its_chore_and_omits_the_name_it_cannot_resolve() -> None:
    """Deleting a chore must not rewrite history or blank out the feed.

    The entry records that points really did change hands, so it stays. What it
    cannot do is name the chore, and the key is dropped rather than sent empty -
    the card renders its generic wording when `chore_name` is absent.
    """
    transactions = [
        _txn(id="txn_ghost", created_at=_at(10), chore_id="chore_deleted", points=7),
    ]

    (event,) = _feed(transactions)

    assert event["id"] == "txn_ghost", (
        "an entry whose chore has been deleted must still appear - the points it "
        "records were genuinely awarded"
    )
    assert event["points"] == 7
    assert "chore_name" not in event, (
        "an unresolvable chore name must be omitted entirely, not published as an "
        f"empty string; got {event.get('chore_name')!r}"
    )
    assert event["child_name"] == "Krishna", (
        "the rest of the row is unaffected by the missing chore"
    )


def test_a_ledger_entry_outlives_its_reward_and_omits_both_reward_keys() -> None:
    """`reward_name` and `reward_icon` are written together, so they go together."""
    transactions = [
        _txn(
            id="txn_ghost_reward",
            created_at=_at(10),
            event_type=EVENT_REWARD_CLAIMED,
            reward_id="reward_deleted",
            points=-25,
        ),
    ]

    (event,) = _feed(transactions)

    assert event["id"] == "txn_ghost_reward"
    assert event["points"] == -25
    assert "reward_name" not in event, (
        "a deleted reward leaves no name to publish, so the key is omitted"
    )
    assert "reward_icon" not in event, (
        "the icon is only ever set alongside the name; publishing one without the "
        "other would leave the card drawing an icon it cannot label"
    )


def test_a_pending_completion_for_a_deleted_chore_still_appears_unnamed() -> None:
    """The pending branch resolves names too, and must survive the same deletion."""
    pending_completions = [_pending(id="comp_ghost", chore_id="chore_deleted", completed_at=_at(5))]

    (event,) = _feed(pending_completions=pending_completions)

    assert event["id"] == "comp_ghost"
    assert event["event_type"] == EVENT_CHORE_PENDING
    assert "chore_name" not in event, (
        "a completion whose chore has since been deleted is still something the "
        "child did; it appears without a name rather than disappearing"
    )
    assert "pending_points" not in event, (
        "there is no chore left to price, so nothing is at stake to publish"
    )


# ---------------------------------------------------------------------------
# Key omission - payload-size discipline
# ---------------------------------------------------------------------------


def test_a_bare_ledger_entry_carries_only_the_six_always_present_keys() -> None:
    """Every optional key is omitted when empty; this is the minimum row.

    Each key omitted here is bytes reclaimed from the 16KiB attribute budget,
    multiplied by the feed limit. Asserting the exact key set - rather than
    checking a few - is what stops an empty-string default creeping back in.
    """
    transactions = [_txn(id="txn_bare", created_at=_at(0))]

    (event,) = _feed(transactions)

    assert set(event) == {
        "id",
        "event_type",
        "child_id",
        "child_name",
        "points",
        "occurred_at",
    }, (
        "a transaction with no reason, no chore and no reward must publish exactly "
        f"the six universal keys; extras were {sorted(set(event) - {'id', 'event_type', 'child_id', 'child_name', 'points', 'occurred_at'})}"
    )


@pytest.mark.parametrize(
    "optional_key",
    ["reason", "chore_name", "reward_name", "reward_icon", "pending_points"],
)
def test_empty_optional_fields_are_absent_rather_than_published_as_empty(
    optional_key: str,
) -> None:
    """Absence is the contract - `"reason": ""` is not an acceptable substitute."""
    transactions = [_txn(id="txn_bare", created_at=_at(0), reason="", chore_id="", reward_id="")]

    (event,) = _feed(transactions)

    assert optional_key not in event, (
        f"{optional_key!r} has nothing to say and must be omitted; publishing it as "
        f"{event.get(optional_key)!r} spends payload budget on nothing and forces the "
        "card to distinguish empty from absent"
    )


def test_a_reason_a_parent_actually_typed_is_published_verbatim() -> None:
    """`reason` carries only free text, never generated wording."""
    transactions = [
        _txn(
            id="txn_bonus",
            created_at=_at(0),
            event_type=EVENT_POINTS_ADDED,
            points=20,
            reason="Helped carry the groceries without being asked",
        )
    ]

    (event,) = _feed(transactions)

    assert event["reason"] == "Helped carry the groceries without being asked"
    assert event["event_type"] == EVENT_POINTS_ADDED, (
        "the structured event type stays separate from the prose, so the card can "
        "classify and colour the row without parsing the reason"
    )


def test_the_event_type_is_carried_through_untranslated() -> None:
    """The card decides wording from the type; the feed must not editorialise."""
    transactions = [
        _txn(id="a", created_at=_at(4), event_type=EVENT_CHORE_APPROVED),
        _txn(id="b", created_at=_at(3), event_type=EVENT_CHORE_REJECTED, points=0),
        _txn(id="c", created_at=_at(2), event_type=EVENT_REWARD_CLAIMED, points=-10),
        _txn(id="d", created_at=_at(1), event_type=EVENT_POINTS_ADDED, points=3),
        _txn(id="e", created_at=_at(0), event_type=EVENT_POINTS_REMOVED, points=-3),
    ]

    events = _feed(transactions)

    assert [e["event_type"] for e in events] == [
        EVENT_CHORE_APPROVED,
        EVENT_CHORE_REJECTED,
        EVENT_REWARD_CLAIMED,
        EVENT_POINTS_ADDED,
        EVENT_POINTS_REMOVED,
    ], "every stored event type reaches the card unchanged"


# ---------------------------------------------------------------------------
# Pending completions
# ---------------------------------------------------------------------------


def test_a_pending_completion_credits_nothing_and_publishes_what_is_at_stake() -> None:
    """ "I did this, where is it?" is the question this row exists to answer.

    Nothing has been credited yet, so `points` - which the card sums and renders
    as a balance change - must be zero. The chore's worth travels separately as
    `pending_points` so the card can say "5 waiting" without implying "5 earned".
    """
    pending_completions = [_pending(id="comp_hw", chore_id="chore_homework")]

    (event,) = _feed(pending_completions=pending_completions)

    assert event["event_type"] == EVENT_CHORE_PENDING
    assert event["points"] == 0, (
        "no balance has moved, so a pending row must contribute nothing to any "
        f"points total the card computes; got {event['points']}"
    )
    assert event["pending_points"] == 5, (
        "Do Homework is worth 5, and that is what is at stake awaiting approval"
    )
    assert event["chore_name"] == "Do Homework"
    assert event["child_name"] == "Krishna"
    assert event["id"] == "comp_hw", (
        "the row is keyed by the completion id so an approve/reject action can tie "
        "back to the record"
    )


def test_a_pending_completion_worth_nothing_omits_the_pending_points_key() -> None:
    """A zero-point chore has nothing at stake, so the key is dropped."""
    pending_completions = [_pending(id="comp_free", chore_id="chore_freebie")]

    (event,) = _feed(pending_completions=pending_completions)

    assert event["chore_name"] == "Say Thank You", "the chore still resolves by name"
    assert "pending_points" not in event, (
        "a chore worth 0 points would publish `pending_points: 0`, which reads as "
        "'nothing waiting' - identical to the absent case but costing bytes"
    )


def test_a_pending_row_carries_no_reason_reward_or_icon() -> None:
    """A completion has no ledger fields to borrow, so none appear."""
    pending_completions = [_pending(id="comp_hw")]

    (event,) = _feed(pending_completions=pending_completions)

    assert set(event) == {
        "id",
        "event_type",
        "child_id",
        "child_name",
        "points",
        "occurred_at",
        "chore_name",
        "pending_points",
    }, f"unexpected keys on a pending row: {sorted(event)}"


# ---------------------------------------------------------------------------
# No duplication
# ---------------------------------------------------------------------------


def test_an_approved_chore_and_a_pending_chore_each_produce_exactly_one_row() -> None:
    """Approval replaces the pending row with the ledger row - it never doubles up.

    The two input lists are disjoint by construction: a completion leaves
    `pending_completions` at the moment it gains a ledger entry. This test pins
    that the feed does not re-derive a pending row from an approved completion,
    which would show the same chore twice on the card.
    """
    transactions = [
        _txn(
            id="txn_approved_hw",
            created_at=_at(30),
            chore_id="chore_homework",
            related_id="comp_hw_approved",
            points=5,
        )
    ]
    pending_completions = [
        _pending(id="comp_dishes", chore_id="chore_dishes", completed_at=_at(10))
    ]

    events = _feed(transactions, pending_completions)

    assert len(events) == 2, (
        "one approved chore plus one still-pending chore is two events, not three or "
        f"four; got {[e['id'] for e in events]}"
    )
    assert [e["id"] for e in events] == ["txn_approved_hw", "comp_dishes"]
    assert [e["event_type"] for e in events] == [
        EVENT_CHORE_APPROVED,
        EVENT_CHORE_PENDING,
    ]

    homework_rows = [e for e in events if e.get("chore_name") == "Do Homework"]
    assert len(homework_rows) == 1, (
        "the approved homework must appear once, as its ledger entry - never also as a pending row"
    )
    assert homework_rows[0]["points"] == 5, (
        "and the surviving row is the ledger one, which is the row that carries the "
        "points actually credited"
    )


def test_the_same_chore_completed_twice_yields_two_distinct_rows() -> None:
    """Deduplication is by record, not by chore - repeat work is real activity."""
    pending_completions = [
        _pending(id="comp_first", chore_id="chore_dishes", completed_at=_at(0)),
        _pending(id="comp_second", chore_id="chore_dishes", completed_at=_at(60)),
    ]

    events = _feed(pending_completions=pending_completions)

    assert [e["id"] for e in events] == ["comp_second", "comp_first"], (
        "a chore with a daily limit above one can legitimately be done twice; both "
        "completions are separate records and both belong in the feed"
    )
    assert {e["chore_name"] for e in events} == {"Clear the Dishes"}


# ---------------------------------------------------------------------------
# Timezone safety
# ---------------------------------------------------------------------------
#
# Every assertion below is about ORDER ACROSS A DAYLIGHT-SAVING TRANSITION,
# where two timestamps can read as "01:30" and "01:00" locally while the second
# is genuinely the later instant. There are two traps, not one:
#
#   1. Sorting the *formatted local strings* gets it backwards, because the UTC
#      offset embedded in them changes at the transition.
#   2. Sorting the datetimes themselves gets it backwards too WHENEVER BOTH
#      CARRY THE SAME `tzinfo` OBJECT. Python documents that comparing two aware
#      datetimes with an identical `tzinfo` attribute ignores the offset and
#      compares the naive wall clocks. Only normalising to a common instant -
#      `.astimezone(timezone.utc)` - is actually safe.
#
# Trap 2 is why `_pacific` below is used for the premise assertions rather than
# bare `<` on the raw values, and why the tests split into two families: those
# fed the *stored* representation (which `models.parse_datetime` reconstitutes
# as distinct fixed-offset tzinfos, so it compares correctly) and those fed raw
# `ZoneInfo` datetimes (which do not).


def _pacific(*args: int, fold: int = 0) -> datetime:
    """A wall-clock time in a real DST-observing zone."""
    return datetime(*args, tzinfo=PACIFIC, fold=fold)


def _stored(value: datetime) -> datetime:
    """Round-trip a datetime through storage, as every real timestamp has been.

    Timestamps reach `build_activity_feed` from `PointsTransaction.from_dict` /
    `ChoreCompletion.from_dict`, never straight off a clock. That round trip
    turns a `ZoneInfo` timestamp into a fixed-offset one, so this is the
    representation the production path actually sorts.
    """
    return parse_datetime(format_datetime(value))


def test_ordering_survives_a_daylight_saving_fall_back() -> None:
    """The clock going back must not reorder the feed.

    01:30 PDT is 08:30 UTC; half an hour later the clock rewinds and 01:00 PST is
    09:00 UTC - genuinely *later*, despite reading as an earlier wall-clock time.
    A sort over the local-time strings puts these the wrong way round, which is
    why the sort key is a datetime rather than the formatted string.
    """
    earlier_instant = _stored(_pacific(*FALL_BACK_DAY, 1, 30, fold=0))
    later_instant = _stored(_pacific(*FALL_BACK_DAY, 1, 0, fold=1))

    # Guard the premise: these really do straddle the transition, and the
    # string comparison really is wrong.
    assert earlier_instant < later_instant, "the PST entry is the newer instant"
    assert (
        _pacific(*FALL_BACK_DAY, 1, 0, fold=1).isoformat()
        < _pacific(*FALL_BACK_DAY, 1, 30, fold=0).isoformat()
    ), (
        "the local-time strings sort the opposite way round - this is the trap the "
        "feed must not fall into"
    )

    transactions = [
        _txn(id="txn_pdt", created_at=earlier_instant, reason="before the change"),
        _txn(id="txn_pst", created_at=later_instant, reason="after the change"),
    ]

    events = _feed(transactions)

    assert [e["id"] for e in events] == ["txn_pst", "txn_pdt"], (
        "newest-first means newest by instant; the 01:00 PST entry happened after "
        "the 01:30 PDT one and must lead the feed"
    )
    assert [e["occurred_at"] for e in events] == [
        "2026-11-01T09:00:00Z",
        "2026-11-01T08:30:00Z",
    ], "the published timestamps are normalised to UTC, which is what makes them safe"


def test_ordering_is_right_inside_the_ambiguous_hour_the_fall_back_repeats() -> None:
    """01:30 happens twice on the fall-back day; the two are an hour apart.

    This is the worst case for any string- or wall-clock-based ordering: the two
    entries are textually identical apart from their offset.
    """
    first_pass = _pacific(*FALL_BACK_DAY, 1, 30, fold=0)
    second_pass = _pacific(*FALL_BACK_DAY, 1, 30, fold=1)

    assert first_pass.replace(tzinfo=None) == second_pass.replace(tzinfo=None), (
        "the wall clock reads identically for both, so only the offset distinguishes "
        "them - a timestamp stored without one would make them indistinguishable"
    )

    transactions = [
        _txn(id="txn_first_pass", created_at=_stored(first_pass)),
        _txn(id="txn_second_pass", created_at=_stored(second_pass)),
    ]

    events = _feed(transactions)

    assert [e["id"] for e in events] == ["txn_second_pass", "txn_first_pass"], (
        "both entries read '01:30' locally; the feed must still put the repeat first"
    )
    assert [e["occurred_at"] for e in events] == [
        "2026-11-01T09:30:00Z",
        "2026-11-01T08:30:00Z",
    ]


def test_ordering_survives_the_spring_forward_gap() -> None:
    """An hour that never existed locally must not distort the elapsed time."""
    before_gap = _pacific(*SPRING_FORWARD_DAY, 1, 30)
    after_gap = _pacific(*SPRING_FORWARD_DAY, 3, 30)

    assert before_gap.utcoffset() == timedelta(hours=-8), "01:30 is still PST"
    assert after_gap.utcoffset() == timedelta(hours=-7), "03:30 is already PDT"
    assert after_gap.astimezone(UTC) - before_gap.astimezone(UTC) == (timedelta(hours=1)), (
        "two hours of wall clock, one hour of real time - 02:00-02:59 never happened"
    )

    transactions = [
        _txn(id="txn_pst", created_at=_stored(before_gap)),
        _txn(id="txn_pdt", created_at=_stored(after_gap)),
    ]

    events = _feed(transactions)

    assert [e["id"] for e in events] == ["txn_pdt", "txn_pst"]
    assert [e["occurred_at"] for e in events] == [
        "2026-03-08T10:30:00Z",
        "2026-03-08T09:30:00Z",
    ], "an hour apart in UTC, whatever the local clock claims"


def test_pending_completions_are_sorted_against_transactions_across_a_transition() -> None:
    """The merge point is where an offset-blind sort would do the most damage."""
    pdt_instant = _stored(_pacific(*FALL_BACK_DAY, 1, 45, fold=0))  # 08:45Z
    pst_instant = _stored(_pacific(*FALL_BACK_DAY, 1, 15, fold=1))  # 09:15Z

    transactions = [_txn(id="txn_pdt", created_at=pdt_instant)]
    pending_completions = [_pending(id="comp_pst", completed_at=pst_instant)]

    events = _feed(transactions, pending_completions)

    assert [e["id"] for e in events] == ["comp_pst", "txn_pdt"], (
        "the pending completion happened half an hour after the ledger entry even "
        "though its wall clock reads half an hour earlier"
    )


def test_a_whole_evening_spanning_the_fall_back_comes_back_in_the_right_order() -> None:
    """Six events either side of the transition, handed over shuffled."""
    wall_clocks = [
        (_pacific(*FALL_BACK_DAY, 0, 15), "07:15"),
        (_pacific(*FALL_BACK_DAY, 1, 5, fold=0), "08:05"),
        (_pacific(*FALL_BACK_DAY, 1, 50, fold=0), "08:50"),
        (_pacific(*FALL_BACK_DAY, 1, 10, fold=1), "09:10"),
        (_pacific(*FALL_BACK_DAY, 1, 55, fold=1), "09:55"),
        (_pacific(*FALL_BACK_DAY, 2, 30), "10:30"),
    ]
    # Deliberately not in chronological order on the way in.
    shuffled = [
        wall_clocks[3],
        wall_clocks[0],
        wall_clocks[5],
        wall_clocks[2],
        wall_clocks[4],
        wall_clocks[1],
    ]
    transactions = [
        _txn(id=f"txn_{label}", created_at=_stored(instant)) for instant, label in shuffled
    ]

    events = _feed(transactions)

    assert [e["id"] for e in events] == [
        "txn_10:30",
        "txn_09:55",
        "txn_09:10",
        "txn_08:50",
        "txn_08:05",
        "txn_07:15",
    ], (
        "the ids spell out the true UTC instants; any wall-clock or string ordering "
        "would interleave the 08:xx and 09:xx entries wrongly"
    )


def test_ordering_is_right_for_zoneinfo_timestamps_that_never_touched_storage() -> None:
    """Correctness must not depend on how the datetime happens to be spelled.

    `dt_util.now()` hands back a datetime tagged with HA's shared `ZoneInfo`
    object. Any future caller that builds a feed from freshly minted timestamps
    rather than from round-tripped storage gets the wrong order today.
    """
    earlier_instant = _pacific(*FALL_BACK_DAY, 1, 30, fold=0)  # 08:30Z
    later_instant = _pacific(*FALL_BACK_DAY, 1, 0, fold=1)  # 09:00Z

    assert earlier_instant.astimezone(UTC) < later_instant.astimezone(UTC), (
        "the premise: the 01:00 PST entry really is the later instant"
    )

    transactions = [
        _txn(id="txn_pdt", created_at=earlier_instant),
        _txn(id="txn_pst", created_at=later_instant),
    ]

    events = _feed(transactions)

    assert [e["id"] for e in events] == ["txn_pst", "txn_pdt"], (
        "newest-first must mean newest by instant even when both timestamps carry "
        "the same ZoneInfo object; sorting them without normalising to UTC compares "
        "the wall clocks '01:30' and '01:00' and puts the older entry first"
    )


def test_a_naive_timestamp_sorts_alongside_aware_ones_instead_of_raising() -> None:
    """Hand-edited storage can hold a naive timestamp; comparing it must not blow up.

    Python refuses to order naive against aware datetimes, so an unguarded sort
    would raise straight through the sensor and into the state machine. The naive
    value is read as UTC, which is the same assumption `models.parse_datetime`
    makes everywhere else.
    """
    transactions = [
        _txn(id="txn_aware_old", created_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC)),
        _txn(id="txn_naive", created_at=datetime(2026, 8, 1, 11, 0)),
        _txn(id="txn_aware_new", created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC)),
    ]

    events = _feed(transactions)

    assert [e["id"] for e in events] == [
        "txn_aware_new",
        "txn_naive",
        "txn_aware_old",
    ], "the naive 11:00 is treated as 11:00 UTC and slots between the two aware entries"


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


def test_empty_inputs_produce_an_empty_feed() -> None:
    """A brand-new install has no history and must publish an empty list, not None."""
    events = build_activity_feed(
        transactions=[],
        pending_completions=[],
        children=[],
        chores=[],
        rewards=[],
        limit=40,
    )

    assert events == [], (
        "the activity sensor's state is the length of this list, so it must always be a list"
    )


def test_events_survive_a_household_with_no_children_chores_or_rewards_at_all() -> None:
    """Nothing resolves, but the ledger is still the ledger."""
    transactions = [
        _txn(
            id="txn_orphan",
            created_at=_at(0),
            chore_id="chore_homework",
            reward_id="reward_screen",
            reason="manual adjustment",
            points=4,
        )
    ]

    (event,) = build_activity_feed(
        transactions=transactions,
        pending_completions=[],
        children=[],
        chores=[],
        rewards=[],
        limit=40,
    )

    assert event["points"] == 4
    assert event["reason"] == "manual adjustment"
    assert "chore_name" not in event
    assert "reward_name" not in event
    assert "reward_icon" not in event


def test_a_transaction_for_an_unknown_child_still_appears_with_an_empty_child_name() -> None:
    """A deleted child leaves ledger entries behind, and they are still true.

    `child_name` is deliberately NOT one of the omitted-when-empty keys: the card
    always renders a name slot, so it always receives one. The `child_id` is kept
    so a consumer can still group by child even with no name to show.
    """
    transactions = [
        _txn(id="txn_ghost_child", created_at=_at(0), child_id="child_deleted", points=6)
    ]

    (event,) = _feed(transactions)

    assert event["id"] == "txn_ghost_child"
    assert event["child_id"] == "child_deleted", (
        "the id survives so the entry can still be attributed if the child returns"
    )
    assert event["child_name"] == "", (
        "an unresolvable child name falls back to an empty string rather than raising "
        "a KeyError or dropping the row"
    )
    assert event["points"] == 6


def test_a_pending_completion_for_an_unknown_child_still_appears() -> None:
    """The pending branch resolves the child the same way and fails the same way."""
    pending_completions = [
        _pending(id="comp_ghost_child", child_id="child_deleted", completed_at=_at(0))
    ]

    (event,) = _feed(pending_completions=pending_completions)

    assert event["child_id"] == "child_deleted"
    assert event["child_name"] == ""
    assert event["chore_name"] == "Do Homework", (
        "the chore still resolves - one unresolvable reference does not poison the row"
    )


def test_events_sharing_an_identical_timestamp_are_all_published() -> None:
    """A bulk approval stamps several entries at the same instant; none may be lost."""
    same_instant = _at(0)
    transactions = [
        _txn(id=f"txn_{index}", created_at=same_instant, chore_id="chore_dishes")
        for index in range(4)
    ]

    events = _feed(transactions)

    assert len(events) == 4, (
        "ties in the sort key must not collapse rows together; four approvals in the "
        f"same second are four events, got {len(events)}"
    )
    assert {e["id"] for e in events} == {"txn_0", "txn_1", "txn_2", "txn_3"}


def test_the_inputs_are_not_mutated_by_building_the_feed() -> None:
    """The coordinator hands over its live lists; the feed must only read them."""
    transactions = [_txn(id="txn_1", created_at=_at(10)), _txn(id="txn_2", created_at=_at(0))]
    pending_completions = [_pending(id="comp_1", completed_at=_at(5))]
    original_txn_order = [t.id for t in transactions]

    _feed(transactions, pending_completions, limit=1)

    assert [t.id for t in transactions] == original_txn_order, (
        "sorting must happen on the feed's own list of tuples, never in place on the "
        "coordinator's transaction list - other consumers rely on oldest-first order"
    )
    assert len(pending_completions) == 1
