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

Rest days push the same idea one step further. Before chores had a schedule,
every calendar day was implicitly a working day, so a child whose chores are
only ever assigned Monday to Friday lost their streak every single Monday
morning. That is exactly backwards: the run ended because the family said not
to work, not because the child did nothing. A day the schedule leaves empty is
therefore *neutral* - it neither extends a run nor breaks one, and the walk
steps straight over it.

The honest cost is that rest days are derived from the schedule as it stands
*now*, not as it stood on the day in question. Move dish duty off Sunday and
every past Sunday retroactively becomes a rest day, which can lengthen a streak
a child has already been shown. The alternative is to snapshot each day's
schedule as that day passes - and that is a counter wearing a different hat: a
second store to keep in step with the first, wrong from the moment a parent
edits a chore, needing a scheduled job to write each night, and with nothing to
notice when it stops. Snapshotting would reintroduce precisely the drift this
module exists to prevent. Between a number that silently disagrees with reality
and a number that moves when the rules move, the second is the one a parent can
be told the reason for: they changed the schedule.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import date, timedelta


def _bridged(previous: date, current: date, rest_days: frozenset[date]) -> bool:
    """Do ``previous`` and ``current`` belong to the same unbroken run?

    They do when nothing the child was actually expected to do lies between
    them: either they are adjacent on the calendar, or every day in the gap is
    a rest day. This is the one place the rest-day rule is applied, so the run
    walk and the is-it-still-live check cannot drift apart - they used to be
    two separate ``timedelta(days=1)`` comparisons, and keeping them that way
    would have meant writing the skip logic twice.

    The length check short-circuits before the walk. Gaps here are unbounded -
    a child can have a completion from three years ago - while ``rest_days``
    only ever covers a bounded recent window, so a gap wider than the whole
    rest set cannot possibly be bridged and is not worth iterating.
    """
    gap = (current - previous).days
    if gap <= 1:
        return True
    if gap - 1 > len(rest_days):
        return False
    return all(
        previous + timedelta(days=offset) in rest_days for offset in range(1, gap)
    )


def compute_streaks(
    active_days: Iterable[date],
    today: date,
    rest_days: Collection[date] = frozenset(),
) -> tuple[int, int]:
    """Return ``(current_streak, best_streak)`` for a set of active days.

    An *active day* is a calendar day on which the child got credit for at
    least one chore. Multiple chores on one day count once - a streak measures
    days, not chores.

    A *rest day* is a calendar day on which the schedule asked nothing of this
    child. It is neutral: it neither extends a run nor breaks one, so a run
    steps over it and comes out the same length on the other side. A rest day
    that is *also* active - a child who did a bonus chore on their day off -
    counts as active, because it arrives in ``active_days`` and is a day in the
    run in its own right. Nothing is subtracted for resting, and nothing is
    given away for it either.

    The current streak is the unbroken run of active days ending at today, at
    yesterday, or at the last day before today that was not a rest day.
    Yesterday counts so that a streak does not appear to collapse the instant
    the clock passes midnight; the child has the whole of today to keep it
    alive. Rest days extend that same grace backwards: a run that reached
    Friday is still live on Monday morning if the weekend was scheduled off.
    Once a day the child was genuinely expected to work has been missed the run
    is over and the current streak is zero, though the best streak remembers
    it.

    Days in the future are ignored. They should not occur, but a clock change
    can produce one, and letting one through would inflate a streak in a way
    that is very hard to explain to a seven-year-old.

    ``rest_days`` defaults to empty, which makes every day a working day and
    reproduces the behaviour this function had before schedules existed. That
    default is what the back-compat tests pin.
    """
    rest = frozenset(rest_days)
    days = sorted({day for day in active_days if day <= today})
    if not days:
        return 0, 0

    best = 1
    run = 1
    for previous, current in zip(days, days[1:]):
        run = run + 1 if _bridged(previous, current, rest) else 1
        best = max(best, run)

    # The run is only live if it reaches today, or is separated from today by
    # nothing but rest days. Today itself is always forgiven, which falls out
    # of ``_bridged`` looking only at the days *between* its two arguments.
    if not _bridged(days[-1], today, rest):
        return 0, best

    current_streak = 1
    index = len(days) - 1
    while index > 0 and _bridged(days[index - 1], days[index], rest):
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
