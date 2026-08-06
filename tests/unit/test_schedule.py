"""Tests for the day-of-week scheduling engine.

`schedule.py` is a pure function of a rule and a calendar date, so everything
here is a plain synchronous test - no `hass`, no storage, no clock. That is the
entire reason the module was pulled out: the cases that decide whether a family
trusts the feature are the ones that are close to unobservable from a running
container. A rotation that slips by one on the morning the clocks change, an
anchor a parent set for next month, a rotation order with three children and a
chore due twice a week - each of them is one line here and a week of confused
bug reports otherwise.

The calendar used throughout is real. 2026-08-03 is a Monday and 2026-08-08 a
Saturday; the DST dates are the America/Los_Angeles transitions the e2e suite
pins its timezone to.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from custom_components.choremander import schedule as schedule_module
from custom_components.choremander.const import DAYS_OF_WEEK
from custom_components.choremander.models import Chore
from custom_components.choremander.schedule import (
    DATE_OVERRIDE_RETENTION_DAYS,
    DAY_NAMES,
    DEFAULT_ANCHOR,
    ROTATION_MODES,
    ROTATION_NONE,
    ROTATION_OCCURRENCE,
    ROTATION_WEEK,
    ChoreSchedule,
    assignees_on,
    prune_date_overrides,
    week_start,
    weekday_name,
)

# The reference week. MONDAY is a real Monday, so `MONDAY + timedelta(days=n)`
# walks a real calendar week and the weekday names below are not wishful.
MONDAY = date(2026, 8, 3)
TUESDAY = date(2026, 8, 4)
WEDNESDAY = date(2026, 8, 5)
THURSDAY = date(2026, 8, 6)
FRIDAY = date(2026, 8, 7)
SATURDAY = date(2026, 8, 8)
SUNDAY = date(2026, 8, 9)
NEXT_MONDAY = date(2026, 8, 10)
NEXT_SATURDAY = date(2026, 8, 15)
NEXT_SUNDAY = date(2026, 8, 16)

WEEK = [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY]

KRISHNA = "child_krishna"
MAYA = "child_maya"
RADHA = "child_radha"


def resolve(days: list[date], **fields) -> list[list[str]]:
    """Resolve a schedule over several days at once, for run-shaped assertions."""
    rule = ChoreSchedule(**fields)
    return [assignees_on(rule, day) for day in days]


# ---------------------------------------------------------------------------
# The base case: everything that exists today keeps working
# ---------------------------------------------------------------------------


def test_a_chore_with_no_schedule_at_all_goes_to_everyone_assigned() -> None:
    """Every chore in every existing install looks exactly like this.

    An empty schedule has to reproduce the pre-scheduling behaviour on every
    day of the week, or shipping this feature un-assigns the installed base.
    """
    rule = ChoreSchedule(assigned_to=[KRISHNA, MAYA])

    assert resolve(WEEK, assigned_to=[KRISHNA, MAYA]) == [[KRISHNA, MAYA]] * 7
    assert assignees_on(rule, date(2030, 1, 1)) == [KRISHNA, MAYA], (
        "and it must still hold years out, since nothing about it depends on the date"
    )


def test_an_empty_due_days_list_means_every_day_of_the_week() -> None:
    """`[]` is "no restriction", not "no days".

    Reading it the other way is the single mistake that would silently empty
    every card in the installed base, because every chore stored today has an
    empty `due_days`.
    """
    assert resolve(WEEK, due_days=[], assigned_to=[KRISHNA]) == [[KRISHNA]] * 7


def test_a_chore_with_nobody_assigned_and_no_rotation_goes_to_nobody() -> None:
    assert assignees_on(ChoreSchedule(), MONDAY) == []


# ---------------------------------------------------------------------------
# Layer 3: due days
# ---------------------------------------------------------------------------


def test_a_due_day_list_hides_the_chore_on_every_other_day() -> None:
    """This alone is the fix for "'bigger' chores only certain days"."""
    resolved = resolve(WEEK, due_days=["monday", "friday"], assigned_to=[KRISHNA])

    assert resolved == [[KRISHNA], [], [], [], [KRISHNA], [], []]


def test_due_days_are_matched_against_the_real_weekday_not_a_position() -> None:
    """Each name must select exactly its own day, all seven of them."""
    for index, name in enumerate(DAY_NAMES):
        resolved = resolve(WEEK, due_days=[name], assigned_to=[KRISHNA])
        assert resolved[index] == [KRISHNA], f"{name} did not select WEEK[{index}]"
        assert sum(1 for day in resolved if day) == 1, f"{name} selected more than one day"


def test_due_day_names_are_matched_case_insensitively() -> None:
    """A hand-written automation may well send "Monday".

    The UI writes lowercase, but the service and YAML surfaces do not enforce
    it, and a chore that silently never came due because of a capital letter
    would look exactly like the feature being broken.
    """
    assert assignees_on(
        ChoreSchedule(due_days=["Monday", "FRIDAY"], assigned_to=[KRISHNA]), MONDAY
    ) == [KRISHNA]


# ---------------------------------------------------------------------------
# Layer 2: permanent weekday overrides
# ---------------------------------------------------------------------------


def test_a_day_override_replaces_the_assignees_for_that_weekday() -> None:
    resolved = resolve(
        WEEK,
        assigned_to=[KRISHNA],
        day_overrides={"saturday": [MAYA]},
    )

    assert resolved[5] == [MAYA], "Saturday belongs to Maya"
    assert resolved[0] == [KRISHNA], "and every other day is untouched"


def test_a_day_override_can_add_a_day_that_due_days_excludes() -> None:
    """Overrides sit *above* the due-day check, not inside it.

    "It is a weekday chore, except Radha also does it on Sundays" has to be
    expressible without rewriting the due days and losing the underlying rule.
    """
    resolved = resolve(
        WEEK,
        due_days=["monday", "tuesday", "wednesday", "thursday", "friday"],
        assigned_to=[KRISHNA],
        day_overrides={"sunday": [RADHA]},
    )

    assert resolved[6] == [RADHA]
    assert resolved[5] == [], "Saturday is still off - only the overridden day is added"


def test_an_empty_day_override_means_explicitly_nobody() -> None:
    """ "We stopped doing this on Saturdays" without deleting the rule."""
    resolved = resolve(WEEK, assigned_to=[KRISHNA], day_overrides={"saturday": []})

    assert resolved[5] == []
    assert resolved[6] == [KRISHNA], "the neighbouring day proves the rule still applies"


def test_a_day_override_beats_a_rotation() -> None:
    resolved = resolve(
        [MONDAY, TUESDAY],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=MONDAY.isoformat(),
        day_overrides={"tuesday": [RADHA]},
    )

    assert resolved == [[KRISHNA], [RADHA]]


def test_day_override_keys_are_matched_case_insensitively() -> None:
    assert assignees_on(
        ChoreSchedule(assigned_to=[KRISHNA], day_overrides={"Saturday": [MAYA]}), SATURDAY
    ) == [MAYA]


# ---------------------------------------------------------------------------
# Layer 1: one-off date overrides
# ---------------------------------------------------------------------------


def test_a_date_override_replaces_the_assignees_for_exactly_one_day() -> None:
    resolved = resolve(
        WEEK,
        assigned_to=[KRISHNA],
        date_overrides={SATURDAY.isoformat(): [MAYA]},
    )

    assert resolved[5] == [MAYA]
    assert resolved == [[KRISHNA]] * 5 + [[MAYA], [KRISHNA]]


def test_a_date_override_does_not_leak_into_the_same_weekday_next_week() -> None:
    """ "Just Aug 8" and "every Saturday" are the two scopes the card offers.

    If a one-off silently repeated they would be the same choice, and the
    distinction every calendar app teaches would be a lie here.
    """
    rule = ChoreSchedule(assigned_to=[KRISHNA], date_overrides={SATURDAY.isoformat(): [MAYA]})

    assert assignees_on(rule, SATURDAY) == [MAYA]
    assert assignees_on(rule, NEXT_SATURDAY) == [KRISHNA]


def test_a_date_override_can_add_a_day_that_due_days_excludes() -> None:
    """Do the bins on Tuesday this once, because we are away on Thursday."""
    resolved = resolve(
        WEEK,
        due_days=["thursday"],
        assigned_to=[KRISHNA],
        date_overrides={TUESDAY.isoformat(): [KRISHNA]},
    )

    assert resolved[1] == [KRISHNA], "Tuesday was added by the one-off"
    assert resolved[3] == [KRISHNA], "and Thursday still comes from the rule"
    assert resolved[0] == [] and resolved[2] == []


def test_an_empty_date_override_means_explicitly_nobody() -> None:
    """ "Skip it this Saturday" - a day off, not a deleted rule."""
    resolved = resolve(
        WEEK,
        assigned_to=[KRISHNA],
        date_overrides={SATURDAY.isoformat(): []},
    )

    assert resolved[5] == []
    assert resolved[4] == [KRISHNA] and resolved[6] == [KRISHNA]


def test_no_override_and_an_empty_override_resolve_differently() -> None:
    """The distinction the whole override design rests on.

    `[]` is a decision and an absent key is the absence of one. If they
    collapsed together there would be no way to express a skipped day at all.
    """
    without = ChoreSchedule(assigned_to=[KRISHNA])
    with_empty = ChoreSchedule(assigned_to=[KRISHNA], date_overrides={SATURDAY.isoformat(): []})

    assert assignees_on(without, SATURDAY) == [KRISHNA]
    assert assignees_on(with_empty, SATURDAY) == []


def test_a_date_override_beats_a_day_override() -> None:
    resolved = resolve(
        [SATURDAY, NEXT_SATURDAY],
        assigned_to=[KRISHNA],
        day_overrides={"saturday": [MAYA]},
        date_overrides={SATURDAY.isoformat(): [RADHA]},
    )

    assert resolved == [[RADHA], [MAYA]], (
        "the one-off wins on its own date and the weekday rule resumes after it"
    )


def test_a_date_override_beats_a_rotation() -> None:
    resolved = resolve(
        [MONDAY, TUESDAY],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=MONDAY.isoformat(),
        date_overrides={TUESDAY.isoformat(): [KRISHNA, MAYA]},
    )

    assert resolved == [[KRISHNA], [KRISHNA, MAYA]], (
        "an override may name several children even though a rotation names one"
    )


def test_the_whole_layer_stack_resolves_top_down() -> None:
    """All four layers on one chore at once, each visible on its own day."""
    rule = ChoreSchedule(
        due_days=["monday", "wednesday", "friday", "saturday"],
        assigned_to=[KRISHNA],
        day_overrides={"saturday": [MAYA]},
        date_overrides={WEDNESDAY.isoformat(): [], SUNDAY.isoformat(): [RADHA]},
    )

    assert [assignees_on(rule, day) for day in WEEK] == [
        [KRISHNA],  # Monday - a due day, no override
        [],  # Tuesday - not a due day
        [],  # Wednesday - a due day, explicitly skipped once
        [],  # Thursday - not a due day
        [KRISHNA],  # Friday - a due day
        [MAYA],  # Saturday - permanent weekday override
        [RADHA],  # Sunday - one-off, added despite not being a due day
    ]


# ---------------------------------------------------------------------------
# Occurrence rotation
# ---------------------------------------------------------------------------


def test_a_weekend_chore_alternating_between_two_children_is_stable_week_to_week() -> None:
    """The exact pattern issue #9 describes: "the kids alternate" on weekends.

    Two due days and two children divide evenly, so Saturday belongs to the
    same child every week. A family who sees the pair swap over would have no
    reason to believe the rest of the feature either.
    """
    resolved = resolve(
        [SATURDAY, SUNDAY, NEXT_SATURDAY, NEXT_SUNDAY],
        due_days=["saturday", "sunday"],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=SATURDAY.isoformat(),
    )

    assert resolved == [[KRISHNA], [MAYA], [KRISHNA], [MAYA]]


def test_a_daily_chore_alternating_between_two_children_inverts_every_week() -> None:
    """Correct, not a defect - and the reason the card draws real dates.

    Seven occurrences a week shared between two children does not divide
    evenly, so whoever had Monday this week has Tuesday next week. That is what
    "they alternate every day" literally means. An abstract weekly template
    could not render it at all, which is why the schedule sensor publishes
    resolved dates instead.
    """
    two_weeks = [MONDAY + timedelta(days=offset) for offset in range(14)]
    resolved = resolve(
        two_weeks,
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved[:7] == [[KRISHNA], [MAYA]] * 3 + [[KRISHNA]]
    assert resolved[7:] == [[MAYA], [KRISHNA]] * 3 + [[MAYA]]
    assert resolved[0] != resolved[7], "the same weekday flips between the two weeks"


def test_occurrence_rotation_starts_the_cycle_on_the_anchor() -> None:
    """The anchor day itself is the first child in the order, not the second."""
    assert assignees_on(
        ChoreSchedule(
            rotation=ROTATION_OCCURRENCE,
            rotation_order=[KRISHNA, MAYA, RADHA],
            rotation_anchor=MONDAY.isoformat(),
        ),
        MONDAY,
    ) == [KRISHNA]


def test_a_rotation_order_longer_than_the_weeks_due_days_takes_several_weeks() -> None:
    """Three children on a two-day-a-week chore need three weeks to come round.

    Nothing normalises the cycle to a week, and it must not: forcing it would
    mean one child was skipped every week forever.
    """
    weekends = [SATURDAY + timedelta(days=offset) for offset in (0, 1, 7, 8, 14, 15, 21)]
    resolved = resolve(
        weekends,
        due_days=["saturday", "sunday"],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA, RADHA],
        rotation_anchor=SATURDAY.isoformat(),
    )

    assert resolved == [
        [KRISHNA],
        [MAYA],
        [RADHA],
        [KRISHNA],
        [MAYA],
        [RADHA],
        [KRISHNA],
    ], "the cycle runs across week boundaries and returns to the start after six turns"


def test_a_single_child_rotation_order_always_returns_that_child() -> None:
    """A degenerate cycle is still a cycle - `% 1` is always 0."""
    resolved = resolve(
        WEEK,
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved == [[KRISHNA]] * 7


def test_an_occurrence_rotation_only_counts_days_the_chore_is_actually_due() -> None:
    """The cycle advances per occurrence, so skipped weekdays must not consume a turn.

    Counting calendar days instead would make a Mon/Wed/Fri chore with two
    children land on the same child every Monday *and* every Friday.
    """
    rule = ChoreSchedule(
        due_days=["monday", "wednesday", "friday"],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert [assignees_on(rule, day) for day in (MONDAY, WEDNESDAY, FRIDAY)] == [
        [KRISHNA],
        [MAYA],
        [KRISHNA],
    ]


# ---------------------------------------------------------------------------
# Week rotation
# ---------------------------------------------------------------------------


def test_a_week_rotation_hands_a_whole_week_to_one_child_then_swaps() -> None:
    """ "Maya has kitchen duty this week" - the other meaning of "alternate"."""
    two_weeks = [MONDAY + timedelta(days=offset) for offset in range(14)]
    resolved = resolve(
        two_weeks,
        rotation=ROTATION_WEEK,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved[:7] == [[KRISHNA]] * 7
    assert resolved[7:] == [[MAYA]] * 7


def test_a_week_rotation_changes_hands_on_monday_whatever_weekday_the_anchor_was() -> None:
    """A parent anchoring on the Thursday they set the chore up expects weeks, not
    Thursday-to-Thursday blocks.

    Normalising both ends to their Monday is what keeps the card's week columns
    and the rotation talking about the same thing.
    """
    rule = ChoreSchedule(
        rotation=ROTATION_WEEK,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=THURSDAY.isoformat(),
    )

    assert assignees_on(rule, MONDAY) == [KRISHNA], "the Monday *before* the anchor"
    assert assignees_on(rule, SUNDAY) == [KRISHNA], "still the anchor's own week"
    assert assignees_on(rule, NEXT_MONDAY) == [MAYA], "the swap lands on Monday"


def test_a_week_rotation_still_respects_due_days() -> None:
    """Owning the week does not mean the chore exists every day of it."""
    resolved = resolve(
        WEEK,
        due_days=["saturday"],
        rotation=ROTATION_WEEK,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved == [[], [], [], [], [], [KRISHNA], []]


def test_a_week_rotation_with_three_children_cycles_every_three_weeks() -> None:
    mondays = [MONDAY + timedelta(weeks=offset) for offset in range(4)]
    resolved = resolve(
        mondays,
        rotation=ROTATION_WEEK,
        rotation_order=[KRISHNA, MAYA, RADHA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved == [[KRISHNA], [MAYA], [RADHA], [KRISHNA]]


# ---------------------------------------------------------------------------
# Where the rotation order comes from
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rotation", [ROTATION_OCCURRENCE, ROTATION_WEEK])
def test_an_empty_rotation_order_falls_back_to_the_assigned_children(rotation: str) -> None:
    """Turning a rotation on must not require re-typing the roster.

    The assignee list is already the set of children who do this chore; making
    a parent restate it as an order before anything happens would make the
    feature look broken on first use.
    """
    resolved = resolve(
        [MONDAY, NEXT_MONDAY],
        rotation=rotation,
        assigned_to=[KRISHNA, MAYA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved == [[KRISHNA], [MAYA]], (
        "the assignee order is the rotation order until a parent states another one"
    )


@pytest.mark.parametrize("rotation", [ROTATION_OCCURRENCE, ROTATION_WEEK])
def test_a_rotation_with_no_children_anywhere_returns_nobody(rotation: str) -> None:
    """Visibly empty beats invented. There is no id that could be returned here."""
    assert assignees_on(ChoreSchedule(rotation=rotation), MONDAY) == []


def test_an_explicit_rotation_order_overrides_the_assignee_order() -> None:
    resolved = resolve(
        [MONDAY, TUESDAY],
        rotation=ROTATION_OCCURRENCE,
        assigned_to=[KRISHNA, MAYA],
        rotation_order=[MAYA, KRISHNA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved == [[MAYA], [KRISHNA]]


def test_a_child_in_the_rotation_order_who_is_not_assigned_still_takes_their_turn() -> None:
    """Dropping them would rotate everyone else into the wrong slot.

    A stale id renders as a missing name, which a parent can see and fix. A
    silently shortened cycle puts the whole family out of phase with nothing to
    look at.
    """
    resolved = resolve(
        [MONDAY, TUESDAY, WEDNESDAY],
        rotation=ROTATION_OCCURRENCE,
        assigned_to=[KRISHNA],
        rotation_order=[KRISHNA, "child_who_left", MAYA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert resolved == [[KRISHNA], ["child_who_left"], [MAYA]]


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------


def test_the_default_anchor_is_a_monday() -> None:
    """A week rotation with no anchor must still change hands on a Monday.

    Any other default date would start everyone's weeks on some arbitrary
    weekday, disagreeing with the card's columns and with `DAYS_OF_WEEK`.
    """
    assert DEFAULT_ANCHOR.weekday() == 0, f"{DEFAULT_ANCHOR} is not a Monday"


@pytest.mark.parametrize("rotation", [ROTATION_OCCURRENCE, ROTATION_WEEK])
@pytest.mark.parametrize("anchor", ["", "not-a-date", "2026-13-45", "tomorrow"])
def test_a_missing_or_malformed_anchor_behaves_exactly_like_the_default_one(
    rotation: str, anchor: str
) -> None:
    """Garbage in the anchor puts the cycle out of phase, never out of action.

    A rotation that raised on a bad anchor would take the whole coordinator
    refresh down over one mistyped service call.
    """
    days = [MONDAY + timedelta(days=offset) for offset in range(14)]
    explicit = resolve(
        days,
        rotation=rotation,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=DEFAULT_ANCHOR.isoformat(),
    )
    fallback = resolve(
        days, rotation=rotation, rotation_order=[KRISHNA, MAYA], rotation_anchor=anchor
    )

    assert fallback == explicit


def test_an_anchor_carrying_a_timestamp_is_read_as_its_date() -> None:
    """An automation templating `now()` sends a full ISO timestamp."""
    with_time = ChoreSchedule(
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor="2026-08-03T21:15:00+00:00",
    )

    assert assignees_on(with_time, MONDAY) == [KRISHNA]


@pytest.mark.parametrize(
    ("rotation", "the_saturday_before"),
    [(ROTATION_OCCURRENCE, [KRISHNA]), (ROTATION_WEEK, [MAYA])],
)
def test_an_anchor_in_the_future_still_resolves_the_days_before_it(
    rotation: str, the_saturday_before: list[str]
) -> None:
    """ "This rotation starts next month" is a reasonable thing for a parent to do.

    Dates before the anchor count backwards through the order rather than
    failing, so the pattern that is about to start is already legible on the
    card. The two cadences land differently, which is the point: counting back
    one *occurrence* over a two-child weekend chore returns to the same child,
    while counting back one *week* hands it to the other.
    """
    rule = ChoreSchedule(
        due_days=["saturday", "sunday"],
        rotation=rotation,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=NEXT_SATURDAY.isoformat(),
    )

    assert assignees_on(rule, NEXT_SATURDAY) == [KRISHNA], "the anchor itself starts the cycle"
    assert assignees_on(rule, SATURDAY) == the_saturday_before


def test_an_anchor_decades_in_the_past_resolves_without_walking_the_calendar() -> None:
    """Anchors far back are the norm, not an edge case.

    Every unanchored chore already counts from `DEFAULT_ANCHOR`, years ago, on
    every sensor refresh. The arithmetic is closed-form for exactly this
    reason, and the answer has to be right rather than merely fast.
    """
    rule = ChoreSchedule(
        rotation=ROTATION_WEEK,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor="1999-01-04",  # a Monday, 1439 whole weeks before MONDAY
    )

    assert assignees_on(rule, MONDAY) == [MAYA], "an odd number of weeks since the anchor"
    assert assignees_on(rule, NEXT_MONDAY) == [KRISHNA], "and it still swaps every Monday"


@pytest.mark.parametrize("cycles", [1, 2, 13])
def test_shifting_the_anchor_by_whole_cycles_changes_nothing(cycles: int) -> None:
    """A property that must hold for any anchor: the cycle is periodic.

    Two children and a daily chore repeat every two days; moving the anchor by
    a multiple of that must be invisible. If it is not, the arithmetic is doing
    something other than counting.
    """
    days = [MONDAY + timedelta(days=offset) for offset in range(21)]
    order = [KRISHNA, MAYA]

    base = resolve(
        days, rotation=ROTATION_OCCURRENCE, rotation_order=order, rotation_anchor=MONDAY.isoformat()
    )
    shifted = resolve(
        days,
        rotation=ROTATION_OCCURRENCE,
        rotation_order=order,
        rotation_anchor=(MONDAY - timedelta(days=len(order) * cycles)).isoformat(),
    )

    assert shifted == base


# ---------------------------------------------------------------------------
# Calendar edges - the engine works on dates and must never do 24-hour maths
# ---------------------------------------------------------------------------


def test_an_occurrence_rotation_does_not_slip_across_the_spring_dst_transition() -> None:
    """2026-03-08 is 23 hours long in America/Los_Angeles.

    Anything that stepped forward by 86400 seconds instead of one calendar day
    would land back on the 8th and hand the same child two turns in a row. The
    engine takes `date` objects specifically so that cannot happen, and this is
    the test that says so.
    """
    anchor = date(2026, 3, 2)  # the Monday before the transition
    week = [anchor + timedelta(days=offset) for offset in range(9)]
    resolved = resolve(
        week,
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=anchor.isoformat(),
    )

    assert date(2026, 3, 8) in week, "precondition: the spring-forward day is in range"
    assert all(first != second for first, second in pairwise(resolved)), (
        "the alternation must not repeat anywhere across the transition"
    )


def test_an_occurrence_rotation_does_not_slip_across_the_autumn_dst_transition() -> None:
    """2026-11-01 is 25 hours long; the mirror image of the spring case."""
    anchor = date(2026, 10, 26)  # the Monday before the transition
    week = [anchor + timedelta(days=offset) for offset in range(9)]
    resolved = resolve(
        week,
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=anchor.isoformat(),
    )

    assert date(2026, 11, 1) in week, "precondition: the fall-back day is in range"
    assert all(first != second for first, second in pairwise(resolved))


def test_a_week_rotation_changes_hands_on_the_monday_after_a_dst_transition() -> None:
    """The DST Sunday belongs to the week that is ending, not the one starting."""
    rule = ChoreSchedule(
        rotation=ROTATION_WEEK,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor="2026-03-02",
    )

    assert assignees_on(rule, date(2026, 3, 8)) == [KRISHNA], "the transition Sunday"
    assert assignees_on(rule, date(2026, 3, 9)) == [MAYA], "the Monday after it"


def test_an_occurrence_rotation_does_not_slip_across_a_year_boundary() -> None:
    """December has 31 days and the year name changes; neither is arithmetic."""
    anchor = date(2025, 12, 29)  # a Monday
    days = [anchor + timedelta(days=offset) for offset in range(10)]
    resolved = resolve(
        days,
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=anchor.isoformat(),
    )

    assert date(2026, 1, 1) in days, "precondition: the year boundary is in range"
    assert all(first != second for first, second in pairwise(resolved))


def test_a_week_rotation_treats_a_week_spanning_new_year_as_one_week() -> None:
    """ISO weeks do not restart in January and neither does this one."""
    rule = ChoreSchedule(
        rotation=ROTATION_WEEK,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor="2025-12-29",
    )

    assert assignees_on(rule, date(2025, 12, 31)) == [KRISHNA]
    assert assignees_on(rule, date(2026, 1, 1)) == [KRISHNA], (
        "New Year's Day is Thursday of the same week as 29 December"
    )
    assert assignees_on(rule, date(2026, 1, 5)) == [MAYA]


def test_an_occurrence_rotation_counts_a_leap_day() -> None:
    """29 February is a due day like any other; February is not 28 days."""
    anchor = date(2028, 2, 28)  # a Monday, so 29 February is the Tuesday
    resolved = resolve(
        [anchor, anchor + timedelta(days=1), anchor + timedelta(days=2)],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor=anchor.isoformat(),
    )

    assert resolved == [[KRISHNA], [MAYA], [KRISHNA]]


def test_the_same_day_resolves_identically_however_often_it_is_asked() -> None:
    """Purity, asserted. The schedule sensor resolves fourteen days per refresh."""
    rule = ChoreSchedule(
        due_days=["monday", "saturday"],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA, RADHA],
        rotation_anchor=MONDAY.isoformat(),
    )

    assert {tuple(assignees_on(rule, SATURDAY)) for _ in range(50)} == {(MAYA,)}


# ---------------------------------------------------------------------------
# Garbage in, no exception out
# ---------------------------------------------------------------------------


def test_an_unknown_weekday_in_due_days_makes_the_chore_due_on_no_day() -> None:
    """A non-empty list is a restriction even when nothing in it is a weekday.

    Filtering the junk out first would leave an empty list, which means *every*
    day - so a typo would turn a Monday chore into a daily one. Silently doing
    nothing is the far safer failure.
    """
    assert resolve(WEEK, due_days=["funday"], assigned_to=[KRISHNA]) == [[]] * 7


def test_an_unknown_weekday_alongside_a_real_one_leaves_the_real_one_working() -> None:
    resolved = resolve(WEEK, due_days=["monday", "funday"], assigned_to=[KRISHNA])

    assert resolved[0] == [KRISHNA]
    assert resolved[1:] == [[]] * 6


def test_an_unknown_rotation_value_behaves_like_no_rotation() -> None:
    """Degrading to today's behaviour, not to nobody.

    A typo in a service call must not silently empty a family's chore list;
    handing the chore to everyone assigned is wrong in a way they can see.
    """
    assert assignees_on(
        ChoreSchedule(rotation="alphabetical", assigned_to=[KRISHNA, MAYA]), MONDAY
    ) == [KRISHNA, MAYA]


def test_an_unknown_rotation_value_does_not_bypass_due_days() -> None:
    """The unknown-cadence fallback happens below the due-day check, not above it."""
    assert (
        assignees_on(
            ChoreSchedule(rotation="alphabetical", due_days=["friday"], assigned_to=[KRISHNA]),
            MONDAY,
        )
        == []
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"due_days": None},
        {"due_days": "monday"},
        {"due_days": [None, 7, ""]},
        {"rotation": None},
        {"rotation": 3},
        {"rotation_order": None},
        {"rotation_order": "child_krishna"},
        {"rotation_order": [None, 1, ""]},
        {"rotation_anchor": None},
        {"rotation_anchor": 20260803},
        {"day_overrides": None},
        {"day_overrides": ["saturday"]},
        {"day_overrides": {None: [KRISHNA]}},
        {"day_overrides": {"saturday": None}},
        {"day_overrides": {"saturday": "child_maya"}},
        {"date_overrides": None},
        {"date_overrides": {"not-a-date": [KRISHNA]}},
        {"date_overrides": {"2026-08-08": None}},
        {"assigned_to": None},
        {"assigned_to": {KRISHNA: True}},
    ],
    ids=lambda fields: "-".join(f"{key}={value!r}" for key, value in fields.items()),
)
def test_no_shape_of_stored_garbage_makes_the_engine_raise(fields: dict) -> None:
    """The inputs come off a user's disk and out of service calls, unvalidated.

    An exception here surfaces as a failed coordinator refresh - every entity
    in the integration goes unavailable - which is a wildly disproportionate
    response to one malformed row.
    """
    for rotation in ROTATION_MODES:
        rule = ChoreSchedule(**{"rotation": rotation, **fields})
        for day in WEEK:
            assert isinstance(assignees_on(rule, day), list)


def test_junk_entries_are_dropped_from_a_list_of_child_ids() -> None:
    """Only non-empty strings can be a child id; everything else is noise."""
    assert assignees_on(ChoreSchedule(assigned_to=[KRISHNA, None, 7, "", MAYA]), MONDAY) == [
        KRISHNA,
        MAYA,
    ]


def test_a_date_override_key_that_is_not_a_date_never_matches_anything() -> None:
    resolved = resolve(WEEK, assigned_to=[KRISHNA], date_overrides={"someday": [MAYA]})

    assert resolved == [[KRISHNA]] * 7


def test_the_returned_list_is_never_the_stored_one() -> None:
    """A caller building a sensor payload must not be able to edit the chore.

    Every layer returns a fresh list, including the override layers where
    handing the stored object back would have been the obvious implementation.
    """
    assigned = [KRISHNA]
    overridden = [MAYA]
    rule = ChoreSchedule(
        assigned_to=assigned,
        date_overrides={SATURDAY.isoformat(): overridden},
        day_overrides={"sunday": overridden},
    )

    assignees_on(rule, MONDAY).append("intruder")
    assignees_on(rule, SATURDAY).append("intruder")
    assignees_on(rule, SUNDAY).append("intruder")

    assert assigned == [KRISHNA]
    assert overridden == [MAYA]


# ---------------------------------------------------------------------------
# Reading a schedule off a chore
# ---------------------------------------------------------------------------


def test_from_chore_reads_every_scheduling_field_off_a_real_chore() -> None:
    """`Chore` is the only production caller, so its field names are the contract."""
    chore = Chore(
        name="Dishes",
        due_days=["saturday", "sunday"],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor="2026-08-08",
        day_overrides={"sunday": [RADHA]},
        date_overrides={"2026-08-15": []},
        assigned_to=[KRISHNA, MAYA],
    )

    rule = ChoreSchedule.from_chore(chore)

    assert rule == ChoreSchedule(
        due_days=["saturday", "sunday"],
        rotation=ROTATION_OCCURRENCE,
        rotation_order=[KRISHNA, MAYA],
        rotation_anchor="2026-08-08",
        day_overrides={"sunday": [RADHA]},
        date_overrides={"2026-08-15": []},
        assigned_to=[KRISHNA, MAYA],
    )


def test_from_chore_on_a_default_chore_reproduces_the_unscheduled_behaviour() -> None:
    chore = Chore(name="Make Bed", assigned_to=[KRISHNA])

    assert assignees_on(ChoreSchedule.from_chore(chore), SUNDAY) == [KRISHNA]


def test_from_chore_tolerates_an_object_that_predates_every_schedule_field() -> None:
    """Duck-typed on purpose - the engine must not depend on `models` to work.

    A stand-in with only the fields the old model had has to load, because that
    is exactly the shape of the objects this ships alongside.
    """

    @dataclass
    class LegacyChore:
        assigned_to: list[str] = field(default_factory=list)

    rule = ChoreSchedule.from_chore(LegacyChore(assigned_to=[KRISHNA]))

    assert rule == ChoreSchedule(assigned_to=[KRISHNA])
    assert assignees_on(rule, MONDAY) == [KRISHNA]


def test_from_chore_turns_a_none_valued_field_into_its_default() -> None:
    """`None` reaches here from a hand-edited storage file, not from the model."""

    @dataclass
    class NullChore:
        due_days: None = None
        rotation: None = None
        rotation_order: None = None
        rotation_anchor: None = None
        day_overrides: None = None
        date_overrides: None = None
        assigned_to: None = None

    assert ChoreSchedule.from_chore(NullChore()) == ChoreSchedule()


# ---------------------------------------------------------------------------
# Pruning one-off overrides
# ---------------------------------------------------------------------------


def test_pruning_keeps_recent_overrides_and_drops_ancient_ones() -> None:
    today = MONDAY
    overrides = {
        (today - timedelta(days=200)).isoformat(): [KRISHNA],
        (today - timedelta(days=1)).isoformat(): [MAYA],
        today.isoformat(): [RADHA],
    }

    kept = prune_date_overrides(overrides, today)

    assert sorted(kept) == [
        (today - timedelta(days=1)).isoformat(),
        today.isoformat(),
    ]


def test_the_pruning_cutoff_is_inclusive_on_its_last_day() -> None:
    """An off-by-one here silently deletes a day a card is still drawing."""
    today = MONDAY
    edge = (today - timedelta(days=DATE_OVERRIDE_RETENTION_DAYS)).isoformat()
    stale = (today - timedelta(days=DATE_OVERRIDE_RETENTION_DAYS + 1)).isoformat()

    kept = prune_date_overrides({edge: [KRISHNA], stale: [MAYA]}, today)

    assert list(kept) == [edge]


def test_pruning_keeps_overrides_however_far_in_the_future_they_are() -> None:
    """Setting up next term's swap in advance is the point of a one-off."""
    today = MONDAY
    far = (today + timedelta(days=400)).isoformat()

    assert list(prune_date_overrides({far: [KRISHNA]}, today)) == [far]


def test_pruning_preserves_an_empty_override_inside_the_window() -> None:
    """`[]` is a decision - "we are skipping that day" - not an empty slot."""
    today = MONDAY
    kept = prune_date_overrides({today.isoformat(): []}, today)

    assert kept == {today.isoformat(): []}


def test_pruning_discards_keys_that_are_not_dates() -> None:
    """They can never match a day, so keeping them is storage that only grows."""
    today = MONDAY

    kept = prune_date_overrides(
        {"someday": [KRISHNA], "": [MAYA], today.isoformat(): [RADHA]}, today
    )

    assert kept == {today.isoformat(): [RADHA]}


def test_pruning_returns_a_new_mapping_rather_than_editing_the_stored_one() -> None:
    """Storage rows are written from the return value; mutating in place would
    make a failed save leave the chore half-pruned."""
    today = MONDAY
    original = {(today - timedelta(days=200)).isoformat(): [KRISHNA]}

    kept = prune_date_overrides(original, today)

    assert kept == {}
    assert len(original) == 1, "the caller's dict must be untouched"


@pytest.mark.parametrize("garbage", [None, [], "2026-08-03", 7])
def test_pruning_anything_that_is_not_a_mapping_yields_an_empty_one(garbage: object) -> None:
    assert prune_date_overrides(garbage, MONDAY) == {}


def test_a_retention_window_of_zero_keeps_only_today_and_later() -> None:
    today = MONDAY
    overrides = {
        (today - timedelta(days=1)).isoformat(): [KRISHNA],
        today.isoformat(): [MAYA],
    }

    assert list(prune_date_overrides(overrides, today, keep_days=0)) == [today.isoformat()]


# ---------------------------------------------------------------------------
# The seams this module deliberately keeps
# ---------------------------------------------------------------------------


def test_the_engines_day_names_match_the_integrations_day_names_exactly() -> None:
    """`DAY_NAMES` duplicates `const.DAYS_OF_WEEK` so the engine can stay
    Home-Assistant-free. This test is the only thing stopping the two copies
    from drifting, which would make a due day mean one thing in the config flow
    and another in the engine.
    """
    assert list(DAY_NAMES) == list(DAYS_OF_WEEK)


def test_the_schedule_engine_imports_nothing_from_home_assistant_or_the_integration() -> None:
    """Purity asserted at the import level, not just by convention.

    One `from .const import ...` and this module can no longer be exercised
    without a running Home Assistant, which is precisely the cost this design
    paid a duplicated list of weekday names to avoid.
    """
    tree = ast.parse(Path(schedule_module.__file__).read_text())

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [
                alias.name for alias in node.names if alias.name.split(".")[0] == "homeassistant"
            ]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                offenders.append(f"relative import of {node.module or '.'}")
            elif (node.module or "").split(".")[0] == "homeassistant":
                offenders.append(node.module or "")

    assert not offenders, f"schedule.py must import nothing but the standard library: {offenders}"


def test_the_rotation_modes_are_exactly_the_three_the_design_settled_on() -> None:
    """Pinned because the service schema validates against this list."""
    assert ROTATION_MODES == (ROTATION_NONE, ROTATION_OCCURRENCE, ROTATION_WEEK)
    assert ROTATION_MODES == ("none", "occurrence", "week"), (
        "these strings are persisted in storage; renaming one is a migration"
    )


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def test_weekday_name_agrees_with_datetime_weekday_on_a_whole_week() -> None:
    assert [weekday_name(day) for day in WEEK] == list(DAY_NAMES)


def test_week_start_returns_the_monday_of_the_week_containing_a_day() -> None:
    """Every day of one week must map to the same Monday, including the Monday."""
    assert [week_start(day) for day in WEEK] == [MONDAY] * 7
    assert week_start(NEXT_MONDAY) == NEXT_MONDAY, "a Monday is its own week start"


def test_week_start_crosses_a_month_boundary_without_help() -> None:
    assert week_start(date(2026, 1, 1)) == date(2025, 12, 29)
