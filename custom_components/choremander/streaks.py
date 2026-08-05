"""Streak calculation for Choremander.

Streaks are *derived* from the approved completion history rather than kept as
an incrementing counter on the child. That choice is deliberate:

* A counter has to be adjusted by every code path that can add or remove a
  completion. Miss one and the number silently drifts, with no way to notice.
  Rejecting an approved chore is the obvious case, and it is easy to get wrong
  because a streak is per *day* while a rejection is per *completion* - a child
  who did three chores on Tuesday should not lose three days of streak when one
  of them is rejected.
* A counter also needs a scheduled job to notice that a day was missed, which
  means the displayed value is wrong between midnight and whenever that job
  runs, and stays wrong forever if the job never fires.

Deriving from history has neither problem: there is no state to drift, and a
missed day is simply absent from the data. The cost is an O(days) walk on each
refresh, which for a family-sized dataset is nothing.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta


def compute_streaks(active_days: Iterable[date], today: date) -> tuple[int, int]:
    """Return ``(current_streak, best_streak)`` for a set of active days.

    An *active day* is a calendar day on which the child got credit for at
    least one chore. Multiple chores on one day count once - a streak measures
    days, not chores.

    The current streak is the unbroken run of active days ending either today
    or yesterday. Yesterday counts so that a streak does not appear to collapse
    the instant the clock passes midnight; the child has the whole of today to
    keep it alive. Once a full day has been missed the run is over and the
    current streak is zero, though the best streak remembers it.

    Days in the future are ignored. They should not occur, but a clock change
    can produce one, and letting one through would inflate a streak in a way
    that is very hard to explain to a seven-year-old.
    """
    days = sorted({day for day in active_days if day <= today})
    if not days:
        return 0, 0

    best = 1
    run = 1
    for previous, current in zip(days, days[1:]):
        run = run + 1 if current - previous == timedelta(days=1) else 1
        best = max(best, run)

    # The run is only live if it reaches today or yesterday.
    if days[-1] < today - timedelta(days=1):
        return 0, best

    current_streak = 1
    index = len(days) - 1
    while index > 0 and days[index] - days[index - 1] == timedelta(days=1):
        current_streak += 1
        index -= 1

    return current_streak, best


def recent_active_days(
    active_days: Iterable[date], today: date, window: int
) -> list[str]:
    """Return the active days inside the last ``window`` days, as ISO strings.

    This is what the streak card's dot strip draws. Returning only the active
    days - rather than a dense day-by-day array - keeps the sensor payload
    small; the card knows the window and fills in the gaps itself.
    """
    if window <= 0:
        return []

    earliest = today - timedelta(days=window - 1)
    return sorted(
        day.isoformat() for day in set(active_days) if earliest <= day <= today
    )
