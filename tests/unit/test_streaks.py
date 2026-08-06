"""Tests for the derived streak calculation.

`compute_streaks` takes no Home Assistant machinery, so these are plain
table-driven tests over calendar arithmetic. That is the point of pulling it
out: the awkward cases - a gap, a run that ended last month, the day after
midnight - are the ones that used to need a running container to observe.

The rest-day cases at the bottom are the reason this file grew. A child with
nothing scheduled at the weekend used to lose their streak every Monday, and
that bug is only observable at a specific calendar offset from a specific
weekday - exactly the kind of thing that is miserable to reproduce by clicking
around a dashboard and trivial to pin here.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.choremander.streaks import compute_streaks, recent_active_days

TODAY = date(2026, 8, 1)  # a Saturday, which the weekend-schedule cases rely on


def days_ago(*offsets: int) -> list[date]:
    """Calendar days counted back from TODAY, e.g. `days_ago(0, 1)` is today and yesterday."""
    return [TODAY - timedelta(days=offset) for offset in offsets]


# ---------------------------------------------------------------------------
# Current streak
# ---------------------------------------------------------------------------


def test_no_activity_at_all_is_a_zero_streak() -> None:
    assert compute_streaks([], TODAY) == (0, 0)


def test_a_single_day_today_is_a_streak_of_one() -> None:
    assert compute_streaks(days_ago(0), TODAY) == (1, 1)


def test_consecutive_days_ending_today_all_count() -> None:
    current, best = compute_streaks(days_ago(0, 1, 2, 3, 4), TODAY)
    assert current == 5
    assert best == 5


def test_several_chores_on_one_day_still_count_as_one_day() -> None:
    """A streak measures days, not chores.

    This is the case an incrementing counter gets wrong: a child who does three
    chores on Tuesday has a one-day streak, not a three-day one.
    """
    duplicated = days_ago(0, 0, 0, 1, 1)
    assert compute_streaks(duplicated, TODAY) == (2, 2)


def test_a_streak_ending_yesterday_is_still_live() -> None:
    """Midnight must not visibly break a streak.

    The child has the whole of today to keep it going. Breaking it the instant
    the date rolls over would show a child a broken streak before they have had
    any chance to do anything about it.
    """
    current, best = compute_streaks(days_ago(1, 2, 3), TODAY)
    assert current == 3, "yesterday still counts as live"
    assert best == 3


def test_a_streak_that_ended_two_days_ago_is_broken() -> None:
    current, best = compute_streaks(days_ago(2, 3, 4), TODAY)
    assert current == 0, "a whole day was missed, so the run is over"
    assert best == 3, "but the best streak remembers it"


def test_a_gap_resets_the_current_streak_but_not_the_best() -> None:
    #  best run of 4 back in July, then a gap, then 2 days up to today
    active = days_ago(0, 1) + days_ago(5, 6, 7, 8)
    current, best = compute_streaks(active, TODAY)
    assert current == 2
    assert best == 4


def test_the_best_streak_can_equal_the_current_one() -> None:
    assert compute_streaks(days_ago(0, 1, 2), TODAY) == (3, 3)


def test_input_order_does_not_matter() -> None:
    scrambled = days_ago(2, 0, 4, 1, 3)
    assert compute_streaks(scrambled, TODAY) == (5, 5)


# ---------------------------------------------------------------------------
# Boundaries and defensive cases
# ---------------------------------------------------------------------------


def test_days_in_the_future_are_ignored() -> None:
    """A clock change must not be able to inflate a streak.

    Future-dated activity should not happen, but a timezone change or a system
    clock correction can produce it, and a streak that inexplicably jumped is
    very hard to explain to a child.
    """
    future = [TODAY + timedelta(days=1), TODAY + timedelta(days=2)]
    current, best = compute_streaks(future + days_ago(0, 1), TODAY)
    assert current == 2
    assert best == 2


def test_only_future_activity_yields_nothing() -> None:
    assert compute_streaks([TODAY + timedelta(days=3)], TODAY) == (0, 0)


def test_a_run_across_a_month_boundary_is_unbroken() -> None:
    """July has 31 days; the arithmetic must not assume 30 or care about names."""
    active = [date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 1)]
    assert compute_streaks(active, date(2026, 8, 1)) == (3, 3)


def test_a_run_across_a_year_boundary_is_unbroken() -> None:
    active = [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1)]
    assert compute_streaks(active, date(2026, 1, 1)) == (3, 3)


def test_a_run_across_a_leap_day_is_unbroken() -> None:
    active = [date(2028, 2, 28), date(2028, 2, 29), date(2028, 3, 1)]
    assert compute_streaks(active, date(2028, 3, 1)) == (3, 3)


def test_a_very_old_single_day_leaves_only_a_best_of_one() -> None:
    current, best = compute_streaks([date(2020, 1, 1)], TODAY)
    assert current == 0
    assert best == 1


@pytest.mark.parametrize("length", [1, 2, 7, 30, 365])
def test_an_unbroken_run_of_any_length_reports_that_length(length: int) -> None:
    active = days_ago(*range(length))
    assert compute_streaks(active, TODAY) == (length, length)


def test_the_best_streak_is_never_less_than_the_current_one() -> None:
    """A property that must hold whatever the input.

    The fork this idea came from maintains the two numbers independently and
    can end up with a best streak lower than the current one after an undo.
    Deriving both from the same walk makes that impossible.
    """
    for start in range(0, 12):
        active = days_ago(*range(start, start + 4))
        current, best = compute_streaks(active, TODAY)
        assert best >= current


# ---------------------------------------------------------------------------
# Rest days
# ---------------------------------------------------------------------------


def test_a_run_survives_a_scheduled_day_off_in_the_middle() -> None:
    """A day the family asked for nothing must not read as a day the child failed.

    This is the whole point of the parameter. Without it, any chore that is not
    scheduled every single day guarantees a streak of at most the length of its
    busiest stretch.
    """
    active = days_ago(0, 1, 3, 4)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(2)))
    assert current == 4, "the day off is stepped over, not counted as a miss"
    assert best == 4


def test_a_rest_day_does_not_lengthen_the_streak_it_bridges() -> None:
    """Neutral means neutral in both directions.

    A rest day must not quietly award a day. Four active days either side of a
    day off is a streak of four, not five - otherwise a family could inflate
    every child's number by scheduling less work.
    """
    active = days_ago(0, 1, 3, 4)
    current, _ = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(2)))
    assert current == 4, "four active days is a streak of four"


def test_a_run_survives_several_consecutive_rest_days() -> None:
    """A long holiday is still a holiday.

    Nothing in the rule caps the number of days that may be skipped: what makes
    a day skippable is that the schedule was empty, not how many neighbours
    share that property.
    """
    active = days_ago(0, 1, 5, 6)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(2, 3, 4)))
    assert current == 4
    assert best == 4


def test_a_scheduled_day_the_child_skipped_still_breaks_the_run() -> None:
    """Rest days must not become a blanket amnesty.

    If the gap contains even one day the child was expected to work, the run is
    broken. A rule that forgave a miss because some *other* nearby day was a
    rest day would make the streak meaningless.
    """
    active = days_ago(0, 1, 3, 4)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(6, 7)))
    assert current == 2, "two days ago was a working day and it was missed"
    assert best == 2


def test_a_partly_rested_gap_still_breaks_the_run() -> None:
    """One working day inside a gap is enough to break it.

    The check is over every day in the gap, not a majority of them.
    """
    active = days_ago(0, 4)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(1, 2)))
    assert current == 1, "three days ago was scheduled and missed"
    assert best == 1


def test_a_rest_day_today_does_not_break_a_run_that_reached_yesterday() -> None:
    """Today was already forgiven; being a rest day must not make it worse.

    The pre-schedule grace period and the rest-day rule overlap here, and the
    two must compose rather than fight - an off day today is the *most* benign
    day there is.
    """
    active = days_ago(1, 2, 3)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(0)))
    assert current == 3
    assert best == 3


def test_a_run_that_ended_before_a_stretch_of_rest_days_is_still_live() -> None:
    """The grace period extends backwards through rest days.

    A run that ended two days ago is dead under the old rule. If both yesterday
    and today were scheduled off, the child has not yet had a chance to
    continue it, so it must still be live - this is the Monday-morning case in
    miniature.
    """
    active = days_ago(2, 3, 4)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(0, 1)))
    assert current == 3, "the child has not been asked to do anything since"
    assert best == 3


def test_a_weekday_only_schedule_survives_the_weekend() -> None:
    """The bug this parameter exists to fix, at the exact offset it appears.

    A child whose chores run Monday to Friday reaches Monday morning with their
    last activity three calendar days behind them. Under the old rule that is a
    dead run, every single week, forever.
    """
    monday = date(2026, 8, 3)
    weekdays = [date(2026, 7, day) for day in (27, 28, 29, 30, 31)]
    weekend = frozenset({date(2026, 8, 1), date(2026, 8, 2)})

    current, best = compute_streaks(weekdays, monday, rest_days=weekend)
    assert current == 5, "Friday's run is still live on Monday morning"
    assert best == 5

    assert compute_streaks(weekdays, monday) == (
        0,
        5,
    ), "and this is what it used to do, which is the bug"


def test_a_weekday_only_schedule_keeps_accumulating_week_after_week() -> None:
    """The run has to grow across the weekend, not merely survive it.

    Surviving would be enough to stop the number resetting; it would not be
    enough for a child to ever see a streak longer than five.
    """
    monday = date(2026, 8, 3)
    active = [date(2026, 7, day) for day in (27, 28, 29, 30, 31)] + [monday]
    weekend = frozenset({date(2026, 7, 25), date(2026, 7, 26), date(2026, 8, 1), date(2026, 8, 2)})

    assert compute_streaks(active, monday, rest_days=weekend) == (6, 6)


def test_a_child_with_nothing_scheduled_at_all_gets_no_invented_streak() -> None:
    """Every day being a rest day is a real configuration, not a bad input.

    A child added to the household before anyone assigns them a chore has an
    entirely empty schedule. That must produce zero, not a crash and not a
    streak conjured out of a fortnight of neutral days.
    """
    every_day = frozenset(days_ago(*range(30)))
    assert compute_streaks([], TODAY, rest_days=every_day) == (0, 0)


def test_a_single_active_day_stays_live_through_an_empty_schedule() -> None:
    """The mirror of the case above, and it is deliberate.

    A child who did their one scheduled chore and has been asked for nothing
    since has not missed anything, so the run is still live. It stays at one:
    the rest days bridge it to today without adding to it.
    """
    rest = frozenset(days_ago(*range(10)))
    current, best = compute_streaks(days_ago(10), TODAY, rest_days=rest)
    assert current == 1
    assert best == 1


def test_a_bonus_chore_on_a_rest_day_counts_as_an_active_day() -> None:
    """Working on a day off earns the day; it is not filtered out as noise.

    An implementation that removed rest days from the active set before walking
    would silently punish the keenest child in the family.
    """
    lone_day = days_ago(1)
    current, best = compute_streaks(lone_day, TODAY, rest_days=frozenset(lone_day))
    assert current == 1, "the day was active, whatever the schedule said"
    assert best == 1


def test_a_bonus_chore_on_a_rest_day_extends_the_run_it_sits_in() -> None:
    """The same day counted once, as an active day, in the middle of a run."""
    active = days_ago(0, 1, 2)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(1)))
    assert current == 3, "not two - the rested day was worked, so it counts"
    assert best == 3


def test_the_best_streak_remembers_a_run_that_spanned_rest_days() -> None:
    """Rest days apply to history, not only to the live run.

    The best streak is walked with the same rule, so an old run that stepped
    over a day off is remembered at its full length.
    """
    active = days_ago(16, 17, 19, 20) + days_ago(0)
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(18)))
    assert current == 1, "the old run is long dead"
    assert best == 4, "but it was four days, not two and two"


def test_an_ancient_active_day_is_not_bridged_by_a_small_rest_set() -> None:
    """The short-circuit in the gap check must not change the answer.

    A years-old completion is separated from today by far more days than the
    rest set could ever contain, so the walk is skipped - but only because the
    result is already known to be `False`.
    """
    active = [date(2020, 1, 1), *days_ago(0)]
    current, best = compute_streaks(active, TODAY, rest_days=frozenset(days_ago(1, 2)))
    assert current == 1
    assert best == 1


def test_rest_days_outside_the_history_change_nothing() -> None:
    """Rest days are consulted, never enumerated.

    The schedule will happily report rest days from before the child existed
    and from next month. Neither may perturb the answer.
    """
    active = days_ago(0, 1, 2)
    noise = frozenset([date(2019, 5, 5), TODAY + timedelta(days=3), TODAY - timedelta(days=400)])
    assert compute_streaks(active, TODAY, rest_days=noise) == compute_streaks(active, TODAY)


@pytest.mark.parametrize("container", [frozenset, set, list, tuple])
def test_rest_days_may_be_any_collection(container: type) -> None:
    """The caller derives these from a schedule and should not have to convert.

    The coordinator builds this per child per refresh; forcing a particular
    container type at the call site is the kind of friction that gets papered
    over with a `list(...)` that allocates for nothing.
    """
    active = days_ago(0, 1, 3)
    rest = container(days_ago(2))
    assert compute_streaks(active, TODAY, rest_days=rest) == (3, 3)


def test_the_best_streak_is_never_less_than_the_current_one_with_rest_days() -> None:
    """The same property as before, re-checked now that two walks share a rule.

    Both the best-streak walk and the current-streak walk go through the same
    gap check, so they cannot disagree about what "consecutive" means. This
    asserts it across a sliding window rather than trusting the reading.
    """
    rest = frozenset(days_ago(2, 5, 9))
    for start in range(0, 12):
        active = days_ago(*range(start, start + 4))
        current, best = compute_streaks(active, TODAY, rest_days=rest)
        assert best >= current


# ---------------------------------------------------------------------------
# Back-compatibility of the default
# ---------------------------------------------------------------------------

# Every expectation from the sections above, restated as data. Any change to
# the walk that alters a pre-schedule answer has to break one of these.
PRE_SCHEDULE_EXPECTATIONS = [
    ([], (0, 0)),
    (days_ago(0), (1, 1)),
    (days_ago(0, 1, 2, 3, 4), (5, 5)),
    (days_ago(0, 0, 0, 1, 1), (2, 2)),
    (days_ago(1, 2, 3), (3, 3)),
    (days_ago(2, 3, 4), (0, 3)),
    (days_ago(0, 1) + days_ago(5, 6, 7, 8), (2, 4)),
    (days_ago(0, 1, 2), (3, 3)),
    (days_ago(2, 0, 4, 1, 3), (5, 5)),
    ([TODAY + timedelta(days=1), TODAY + timedelta(days=2), *days_ago(0, 1)], (2, 2)),
    ([TODAY + timedelta(days=3)], (0, 0)),
    ([date(2020, 1, 1)], (0, 1)),
]


@pytest.mark.parametrize("active, expected", PRE_SCHEDULE_EXPECTATIONS)
def test_omitting_rest_days_reproduces_the_pre_schedule_behaviour_exactly(
    active: list[date], expected: tuple[int, int]
) -> None:
    """Adding the parameter must be invisible to every existing caller.

    Streaks are already on screen in front of children. A refactor that shifted
    a single number by one would be reported as a bug by a seven-year-old, and
    correctly so. Passing no rest days, or an empty set in any container, must
    give the answer this module gave before scheduling existed.
    """
    assert compute_streaks(active, TODAY) == expected, "the two-argument call"
    for empty in (frozenset(), set(), [], ()):
        assert compute_streaks(active, TODAY, empty) == expected, f"positional {empty!r}"
        assert compute_streaks(active, TODAY, rest_days=empty) == expected, f"keyword {empty!r}"


# ---------------------------------------------------------------------------
# The dot strip window
# ---------------------------------------------------------------------------


def test_recent_active_days_returns_iso_strings_in_order() -> None:
    assert recent_active_days(days_ago(2, 0, 1), TODAY, 30) == [
        "2026-07-30",
        "2026-07-31",
        "2026-08-01",
    ]


def test_recent_active_days_excludes_anything_outside_the_window() -> None:
    active = days_ago(0, 5, 40)
    inside = recent_active_days(active, TODAY, 7)
    assert inside == ["2026-07-27", "2026-08-01"], "the 40-day-old entry is dropped"


def test_recent_active_days_window_is_inclusive_of_both_ends() -> None:
    """A window of 7 means today plus the six days before it."""
    active = days_ago(0, 6, 7)
    assert recent_active_days(active, TODAY, 7) == ["2026-07-26", "2026-08-01"]


def test_recent_active_days_deduplicates() -> None:
    assert recent_active_days(days_ago(1, 1, 1), TODAY, 30) == ["2026-07-31"]


def test_recent_active_days_ignores_the_future() -> None:
    assert recent_active_days([TODAY + timedelta(days=1)], TODAY, 30) == []


@pytest.mark.parametrize("window", [0, -1])
def test_a_non_positive_window_returns_nothing(window: int) -> None:
    assert recent_active_days(days_ago(0), TODAY, window) == []
