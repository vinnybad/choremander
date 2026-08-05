"""Tests for the derived streak calculation.

`compute_streaks` takes no Home Assistant machinery, so these are plain
table-driven tests over calendar arithmetic. That is the point of pulling it
out: the awkward cases - a gap, a run that ended last month, the day after
midnight - are the ones that used to need a running container to observe.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.choremander.streaks import compute_streaks, recent_active_days

TODAY = date(2026, 8, 1)


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
