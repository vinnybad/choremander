"""Day-of-week chore scheduling for Choremander.

Who is on a chore on a given day is *computed* from a rule plus the date. It is
never written down per day. That is the same bet ``streaks.py`` makes about
history, made here for the same reasons:

* A stored per-day roster has to be extended into the future by a scheduled
  job. Between a parent editing the rule and that job next running, the card
  shows the old answer - and if the job never fires it shows it forever.
* A rotation cursor ("Krishna went last") has to be advanced by exactly one
  code path. Deleting a chore, reordering the children, or a child leaving the
  household all have to nudge it correctly, and a single miss puts the family
  permanently out of phase with nothing to notice it by.

A pure function of the date has neither failure mode: ask twice and you get the
same answer, ask about next August and you get an answer at all. The cost is
that changing a rule reshapes the past as well as the future, which is the
trade-off the design accepted explicitly.

The module imports nothing from Home Assistant and nothing else from the
integration, so every awkward case - an anchor in the future, a rotation order
longer than the number of due days, a cycle crossing a year boundary - is
reachable from a plain unit test with no running container. ``DAY_NAMES``
duplicates ``const.DAYS_OF_WEEK`` rather than importing it for that reason
alone; ``tests/unit/test_schedule.py`` asserts the two never drift apart.

Everything here works on ``datetime.date``. Nothing converts to a datetime and
adds seconds: on the two days a year the local clock jumps, "24 hours later" is
not "tomorrow", and a rotation that slipped by a day every spring would be very
close to undiagnosable from a bug report.

Nothing in this module raises. It is fed storage rows that predate every field
it reads, hand-edited YAML, and service calls, and the honest failure for a
schedule it cannot understand is to fall back to the behaviour the product had
before schedules existed - not to take the coordinator refresh down with it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

# Lowercase, Monday first, so the index of a name is exactly ``date.weekday()``.
# Kept in step with ``const.DAYS_OF_WEEK`` by a test rather than an import - see
# the module docstring for why this module stays free of project imports.
DAY_NAMES: Final = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# The two cadences exist because "the kids alternate" means two different things
# to two different families:
#
# * OCCURRENCE - "they take turns each time." Dishes on Saturday and Sunday with
#   two children gives Saturday to one and Sunday to the other, stable week to
#   week.
# * WEEK - "Maya has kitchen duty this week." The whole week goes to one child
#   however many days the chore falls on.
#
# A *daily* chore on OCCURRENCE with two children inverts every week: seven
# occurrences shared between two people does not divide evenly. That is what
# "alternate every day" literally means, and it is why the schedule card draws
# real dates instead of an abstract weekly template.
ROTATION_NONE: Final = "none"
ROTATION_OCCURRENCE: Final = "occurrence"
ROTATION_WEEK: Final = "week"
ROTATION_MODES: Final = (ROTATION_NONE, ROTATION_OCCURRENCE, ROTATION_WEEK)

# A cycle has to be counted from somewhere, and most chores will never carry an
# anchor of their own. 2024-01-01 is a Monday, so a week rotation anchored by
# default changes hands on the same boundary the rest of the product calls a
# week; anchoring on any other date would silently start everyone's weeks on a
# Wednesday.
DEFAULT_ANCHOR: Final = date(2024, 1, 1)

# One-off overrides are notes about a single day. Sixty days back is long enough
# that nothing is still looking at them - the schedule card shows two weeks and
# the streak strip thirty days - and short enough that a family who swaps a
# Saturday every week does not grow the storage blob without bound.
DATE_OVERRIDE_RETENTION_DAYS: Final = 60


@dataclass(frozen=True)
class ChoreSchedule:
    """The scheduling rule for one chore, lifted off the model.

    Frozen on purpose. ``assignees_on`` promises to be a pure function of a
    schedule and a date; a schedule that could change between two calls would
    make that quietly untrue, and this object is passed to a sensor that
    resolves fourteen days from it in a loop.

    The fields mirror ``Chore`` one for one and stay flat rather than nesting
    under a ``schedule`` key, because a nested dict would have needed a storage
    migration to introduce and every consumer would have had to handle its
    absence anyway.

    ``due_days`` empty means *every day*. That is not a stylistic choice: every
    chore that exists today has an empty ``due_days``, and any other reading
    would silently un-schedule the entire installed base.
    """

    due_days: list[str] = field(default_factory=list)
    rotation: str = ROTATION_NONE
    rotation_order: list[str] = field(default_factory=list)
    rotation_anchor: str = ""
    day_overrides: dict[str, list[str]] = field(default_factory=dict)
    date_overrides: dict[str, list[str]] = field(default_factory=dict)
    assigned_to: list[str] = field(default_factory=list)

    @classmethod
    def from_chore(cls, chore: Any) -> ChoreSchedule:
        """Read a schedule off a ``Chore``.

        Duck-typed via ``getattr`` rather than typed against ``models.Chore``,
        so this module keeps its promise of importing nothing from the rest of
        the integration and tests can drive it with a three-line stub. Every
        lookup carries a default because a chore stored before these fields
        existed loads with them defaulted anyway - there is no version of the
        data where a missing attribute should be an error.
        """
        return cls(
            due_days=getattr(chore, "due_days", None) or [],
            rotation=getattr(chore, "rotation", ROTATION_NONE) or ROTATION_NONE,
            rotation_order=getattr(chore, "rotation_order", None) or [],
            rotation_anchor=getattr(chore, "rotation_anchor", "") or "",
            day_overrides=getattr(chore, "day_overrides", None) or {},
            date_overrides=getattr(chore, "date_overrides", None) or {},
            assigned_to=getattr(chore, "assigned_to", None) or [],
        )


def weekday_name(day: date) -> str:
    """Return the stored lowercase name of a date's weekday."""
    return DAY_NAMES[day.weekday()]


def week_start(day: date) -> date:
    """Return the Monday of the week containing ``day``.

    Weeks are Monday-first everywhere in this product - ``DAYS_OF_WEEK``, the
    schedule card's columns and the week rotation all agree - so this is the one
    place that arithmetic lives.
    """
    return day - timedelta(days=day.weekday())


def assignees_on(schedule: ChoreSchedule, day: date) -> list[str]:
    """Return the child ids on this chore on ``day``, in rotation order.

    This single function is the whole contract of the module. The sensor, the
    pricing calculation and the streak rest-day set all ask it the same
    question, which is what stops three implementations of "is this chore on
    today?" from drifting apart.

    Layers are resolved highest priority first:

    1. A one-off ``date_overrides`` entry for exactly this date. It wins over
       everything, including ``due_days`` - "we swapped, just this Saturday" and
       "do it on Tuesday this once" are the same feature.
    2. A permanent ``day_overrides`` entry for this weekday.
    3. ``due_days``: a non-empty list that does not contain this weekday means
       nobody. An empty list means every day, for the back-compat reason on
       ``ChoreSchedule``.
    4. No rotation - everyone the chore is assigned to, exactly today's
       behaviour.
    5. or 6. the rotation cadence, which names exactly one child.

    Both override layers distinguish an entry of ``[]`` - explicitly nobody, a
    skipped day - from no entry at all. Collapsing those would make it
    impossible to say "not this week" without deleting the rule.

    The returned list is always freshly built, never a stored list handed back
    by reference, because callers build sensor payloads out of it and one
    in-place edit would corrupt the chore it came from.
    """
    override = _override_for(schedule.date_overrides, day.isoformat())
    if override is not None:
        return override

    override = _override_for(schedule.day_overrides, weekday_name(day))
    if override is not None:
        return override

    due_days = _day_names(schedule.due_days)
    if due_days and weekday_name(day) not in due_days:
        return []

    rotation = schedule.rotation if isinstance(schedule.rotation, str) else ROTATION_NONE
    if rotation not in (ROTATION_OCCURRENCE, ROTATION_WEEK):
        # ``none``, and anything unrecognised. An unknown cadence degrades to
        # the pre-scheduling behaviour rather than to nobody: a typo in a
        # service call should not silently empty a family's chore list.
        return _child_ids(schedule.assigned_to)

    order = _child_ids(schedule.rotation_order) or _child_ids(schedule.assigned_to)
    if not order:
        # A rotation with nobody in it. Returning an empty list keeps the chore
        # off every card until a parent fixes it, which is visible; anything
        # else would have to invent a child id.
        return []

    anchor = _anchor(schedule.rotation_anchor)
    if rotation == ROTATION_OCCURRENCE:
        ordinal = _occurrence_ordinal(due_days, anchor, day)
    else:
        ordinal = _week_ordinal(anchor, day)

    # Python's modulo is non-negative for a positive divisor, so a date before
    # the anchor counts backwards through the order instead of failing. An
    # anchor set in the future is a legitimate thing for a parent to do ("this
    # starts next month") and must not make today unresolvable.
    return [order[ordinal % len(order)]]


def prune_date_overrides(
    date_overrides: Any,
    today: date,
    keep_days: int = DATE_OVERRIDE_RETENTION_DAYS,
) -> dict[str, list[str]]:
    """Drop one-off overrides old enough that nothing will ever read them again.

    Called on write rather than on read: pruning where the data is saved means
    the storage blob stays bounded, whereas pruning where it is read would leave
    the file growing forever while merely hiding the growth.

    The cutoff is inclusive - an override exactly ``keep_days`` days old is
    kept, one day older is dropped. Future dates are always kept however far
    out, because a parent setting up next month's swap is the whole point of a
    one-off.

    Keys that are not ISO dates are discarded. They can never match a day, so
    keeping them would let one typo from a hand-written service call sit in
    storage for the lifetime of the install.
    """
    cutoff = today - timedelta(days=max(keep_days, 0))
    kept: dict[str, list[str]] = {}
    for key, value in _as_mapping(date_overrides).items():
        when = _as_date(key)
        if when is not None and when >= cutoff:
            kept[key] = _child_ids(value)
    return kept


# ---------------------------------------------------------------------------
# Cycle arithmetic
# ---------------------------------------------------------------------------


def _occurrence_ordinal(due_days: frozenset[str], anchor: date, day: date) -> int:
    """Return ``day``'s position in the sequence of the chore's due days.

    The anchor's own occurrence is 0, the next due day is 1, and days before the
    anchor count negative. Counting the whole sequence rather than tracking a
    cursor is what makes the answer identical whether it is asked once or a
    thousand times.

    Counted in closed form, one term per due weekday, instead of walking the
    calendar. The default anchor can be years in the past and the schedule
    sensor resolves fourteen days for every chore on every refresh; an O(days)
    walk there would be doing tens of thousands of date additions to answer a
    question that is seven divisions.
    """
    weekdays = _due_weekday_indices(due_days)
    if not weekdays:
        return 0

    if day >= anchor:
        return _count_weekdays(weekdays, anchor, day) - 1
    # Symmetric backwards count. Taking ``[anchor, day]`` as an empty range and
    # calling it 0 would give every date before the anchor the same ordinal.
    return -_count_weekdays(weekdays, day, anchor - timedelta(days=1))


def _week_ordinal(anchor: date, day: date) -> int:
    """Return the number of whole weeks between the anchor's week and ``day``'s.

    Both ends are normalised to their Monday first, so a rotation always changes
    hands at a week boundary rather than on whatever weekday the anchor happened
    to be. The subtraction is then an exact multiple of seven, which is what
    makes the floor division exact for dates before the anchor too.
    """
    return (week_start(day) - week_start(anchor)).days // 7


def _count_weekdays(weekdays: frozenset[int], start: date, end: date) -> int:
    """Count the days in ``[start, end]`` whose weekday is in ``weekdays``."""
    if end < start:
        return 0
    total = 0
    for weekday in weekdays:
        first = start + timedelta(days=(weekday - start.weekday()) % 7)
        if first <= end:
            total += (end - first).days // 7 + 1
    return total


def _due_weekday_indices(due_days: frozenset[str]) -> frozenset[int]:
    """Turn stored day names into ``date.weekday()`` indices.

    An empty ``due_days`` is every day, per ``ChoreSchedule``. Names that are
    not weekdays are dropped here and only here: they still count as "the list
    is non-empty" for the due-day check above, so a chore due on ``["funday"]``
    is due on no day at all rather than quietly reverting to every day.
    """
    if not due_days:
        return frozenset(range(7))
    return frozenset(index for index, name in enumerate(DAY_NAMES) if name in due_days)


# ---------------------------------------------------------------------------
# Coercions
#
# Everything below exists because this module's inputs come from a JSON file on
# a user's disk and from service calls, neither of which is validated against
# the shapes above. Each one narrows to the expected type and gives up quietly.
# ---------------------------------------------------------------------------


def _override_for(overrides: Any, key: str) -> list[str] | None:
    """Look up an override, returning ``None`` only when there is no entry.

    The ``None``/``[]`` distinction is load-bearing: ``[]`` is "explicitly
    nobody" and ``None`` is "this layer has no opinion, ask the next one".
    Returning ``[]`` for both would make it impossible to express a skipped day.

    Matched case-insensitively so ``{"Saturday": [...]}`` from a hand-written
    automation behaves the same as the lowercase form the UI writes.
    """
    for stored_key, value in _as_mapping(overrides).items():
        if isinstance(stored_key, str) and stored_key.lower() == key:
            return _child_ids(value)
    return None


def _child_ids(value: Any) -> list[str]:
    """Copy a stored list of child ids, dropping anything that is not one.

    Ids that do not correspond to a real child are *kept*. Resolving them is not
    this module's job, and a rotation order that silently shrank when a child id
    went stale would rotate everyone else into the wrong slot - a much harder
    thing to notice than a name that fails to render.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _day_names(value: Any) -> frozenset[str]:
    """Normalise stored weekday names for comparison."""
    if not isinstance(value, (list, tuple)):
        return frozenset()
    return frozenset(item.lower() for item in value if isinstance(item, str) and item)


def _as_mapping(value: Any) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def _as_date(value: Any) -> date | None:
    """Parse a stored ISO date, or return ``None``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            # Sliced to the date part so a full timestamp, which is what an
            # automation templating `now()` produces, is read rather than
            # rejected.
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _anchor(value: Any) -> date:
    """Read a rotation anchor, falling back to ``DEFAULT_ANCHOR``.

    An unparseable anchor gives the same cycle as no anchor at all instead of
    breaking the chore. The rotation is then off the phase the parent intended,
    which is visible on the card and fixable; an exception here would take out
    the whole coordinator refresh.
    """
    return _as_date(value) or DEFAULT_ANCHOR
