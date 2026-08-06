"""The resolved schedule window the companion sensor publishes.

`ChoremanderCoordinator.schedule_window` turns the stored rules into the
answer the cards actually need - who is on which chore on which real date -
so that no rotation arithmetic is ever reimplemented in JavaScript. There is
one implementation of "whose turn is it", in `schedule.py`, and this is how it
reaches the frontend.

The payload is indexed rather than spelled out: `children` and `chores` are
lists of ids, and every day names them by position. Two things forced that.
The recorder drops any state whose attributes exceed 16KiB, and spelling the
ids out across a fortnight measures 33,165 bytes for the stress household in
`test_sensor_payload_size.py`. Indexing brings the same worst case to 6,465.

Like the pricing tests, these drive a real coordinator with its `_data` dict
assigned directly - the method only reads `storage`, so there is no config
entry, no `async_load` and no clock beyond the date passed in.
"""

from __future__ import annotations

from datetime import date

from homeassistant.core import HomeAssistant
import pytest

from custom_components.choremander.const import SCHEDULE_WINDOW_DAYS
from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import Child, Chore

# 2026-08-03 is a real Monday, so the reference week below walks a real
# calendar and the weekday names are not wishful.
MONDAY = date(2026, 8, 3)
WEDNESDAY = date(2026, 8, 5)
SUNDAY = date(2026, 8, 9)
NEXT_MONDAY = date(2026, 8, 10)

KRISHNA = "child_krishna"
MAYA = "child_maya"
RADHA = "child_radha"


@pytest.fixture
def make_schedule_coordinator(hass: HomeAssistant):
    """Build a real coordinator over hand-written children and chores."""

    def _make(
        children: list[Child] | None = None,
        chores: list[Chore] | None = None,
    ) -> ChoremanderCoordinator:
        coordinator = ChoremanderCoordinator(hass, "schedule_entry")
        coordinator.storage._data = {
            "children": [c.to_dict() for c in children or []],
            "chores": [c.to_dict() for c in chores or []],
            "rewards": [],
            "completions": [],
            "reward_claims": [],
            "points_name": "Stars",
            "points_icon": "mdi:star",
        }
        return coordinator

    return _make


def child(child_id: str) -> Child:
    """A child with a pinned id, so assertions can name it."""
    return Child(name=child_id, id=child_id)


def chore(chore_id: str, **schedule) -> Chore:
    """A chore carrying nothing but its id and its schedule."""
    return Chore(name=chore_id, id=chore_id, **schedule)


def day_entry(window: dict, offset: int) -> dict:
    """The resolved entry for one day of the window."""
    return window["days"][offset]


def named(window: dict, offset: int) -> dict[str, list[str]]:
    """One day of the window with its indices resolved back to ids.

    Every assertion about *who* is on *what* reads through this rather than
    against raw indices, so a test failure names children and chores instead of
    integers - and so a change to the roster order fails in one place.
    """
    return {
        window["chores"][int(chore_index)]: [window["children"][i] for i in child_indices]
        for chore_index, child_indices in day_entry(window, offset).items()
    }


# ---------------------------------------------------------------------------
# The window itself
# ---------------------------------------------------------------------------


def test_the_window_starts_on_the_monday_of_the_current_week(
    make_schedule_coordinator,
) -> None:
    """Week-aligned, so "this week" in the card is the week the family means.

    Starting at today instead would put a different weekday in the first column
    every day, and "every Saturday" would need paging to reach.
    """
    coordinator = make_schedule_coordinator(children=[child(KRISHNA)])

    for today in (MONDAY, WEDNESDAY, SUNDAY):
        window = coordinator.schedule_window(today)
        assert window["window_start"] == MONDAY.isoformat(), (
            f"every day of the week containing {MONDAY} must produce the same "
            f"window start; {today} produced {window['window_start']}"
        )

    assert coordinator.schedule_window(NEXT_MONDAY)["window_start"] == (NEXT_MONDAY.isoformat()), (
        "a Monday is its own week start, so the window rolls over on Monday"
    )


def test_the_window_covers_this_week_and_next(make_schedule_coordinator) -> None:
    """Two weeks: enough for "every Saturday" and for next week's one-off swap."""
    coordinator = make_schedule_coordinator(children=[child(KRISHNA)])

    window = coordinator.schedule_window(MONDAY)

    assert window["window_days"] == SCHEDULE_WINDOW_DAYS == 14
    assert len(window["days"]) == 14, (
        "`days` is positional - the card reads day N as window_start + N - so "
        f"it must always carry exactly window_days entries, got {len(window['days'])}"
    )


def test_the_roster_is_published_in_household_order(make_schedule_coordinator) -> None:
    """`children` is the key every day's indices are read through."""
    coordinator = make_schedule_coordinator(children=[child(KRISHNA), child(MAYA), child(RADHA)])

    assert coordinator.schedule_window(MONDAY)["children"] == [KRISHNA, MAYA, RADHA]


# ---------------------------------------------------------------------------
# What the payload leaves out
# ---------------------------------------------------------------------------


def test_a_household_that_schedules_nothing_publishes_no_chores(
    make_schedule_coordinator,
) -> None:
    """Every install today, and the reason this sensor costs almost nothing.

    An unscheduled chore resolves to the same `assigned_to` on all fourteen
    days. Publishing that is fourteen copies of what the card already knows, so
    the chore is omitted and the card keeps doing exactly what it does now.
    """
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA), child(MAYA)],
        chores=[
            chore("chore_bed", assigned_to=[KRISHNA]),
            chore("chore_dishes"),
        ],
    )

    window = coordinator.schedule_window(MONDAY)

    assert window["chores"] == [], (
        f"neither chore carries a rule, so neither belongs in the payload; got {window['chores']}"
    )
    assert window["days"] == [{}] * 14, (
        "and every day of the window is empty rather than absent - the card "
        "indexes into this list by day offset"
    )


def test_only_the_chores_carrying_a_rule_reach_the_payload(
    make_schedule_coordinator,
) -> None:
    """The indices in `days` point into `chores`, not into storage order."""
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[
            chore("chore_unscheduled_a", assigned_to=[KRISHNA]),
            chore("chore_weekend", assigned_to=[KRISHNA], due_days=["saturday"]),
            chore("chore_unscheduled_b", assigned_to=[KRISHNA]),
        ],
    )

    window = coordinator.schedule_window(MONDAY)

    assert window["chores"] == ["chore_weekend"]
    assert named(window, 5) == {"chore_weekend": [KRISHNA]}, (
        "the Saturday of the first week is offset 5, and the surviving chore "
        "must be addressed as index 0 of the filtered list rather than index 1 "
        "of storage order - an off-by-one here silently reassigns chores"
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_a_due_days_chore_appears_only_on_its_days(make_schedule_coordinator) -> None:
    """The plainest reading of issue #9: bigger chores only certain days."""
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[chore("chore_bins", assigned_to=[KRISHNA], due_days=["monday"])],
    )

    window = coordinator.schedule_window(MONDAY)

    on_days = [offset for offset in range(14) if day_entry(window, offset)]
    assert on_days == [0, 7], (
        "a Monday chore must fall on the two Mondays of a fortnight and nowhere "
        f"else, got offsets {on_days}"
    )
    assert named(window, 0) == {"chore_bins": [KRISHNA]}


def test_a_rotation_hands_the_chore_round_across_real_dates(
    make_schedule_coordinator,
) -> None:
    """The other reading of issue #9: the kids alternate.

    Resolved to real dates here rather than to a weekly template, because a
    rotation between two children over an odd number of due days genuinely does
    invert from one week to the next.
    """
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA), child(MAYA)],
        chores=[
            chore(
                "chore_dishes",
                assigned_to=[KRISHNA, MAYA],
                due_days=["saturday", "sunday"],
                rotation="occurrence",
                rotation_order=[KRISHNA, MAYA],
                rotation_anchor="2026-08-08",
            )
        ],
    )

    window = coordinator.schedule_window(MONDAY)
    weekend = {offset: named(window, offset) for offset in (5, 6, 12, 13)}

    assert weekend == {
        5: {"chore_dishes": [KRISHNA]},
        6: {"chore_dishes": [MAYA]},
        12: {"chore_dishes": [KRISHNA]},
        13: {"chore_dishes": [MAYA]},
    }, (
        "anchored on the first Saturday, the pair must alternate Sat/Sun and "
        f"repeat the following weekend, got {weekend}"
    )
    assert all(not day_entry(window, offset) for offset in (0, 1, 2, 3, 4)), (
        "and the chore must be absent on the weekdays it is not due"
    )


def test_a_scheduled_chore_assigned_to_nobody_goes_to_the_whole_household(
    make_schedule_coordinator,
) -> None:
    """An empty `assigned_to` has always meant *anyone*, and still does."""
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA), child(MAYA)],
        chores=[chore("chore_tidy", due_days=["monday"])],
    )

    assert named(coordinator.schedule_window(MONDAY), 0) == {"chore_tidy": [KRISHNA, MAYA]}, (
        "a chore nobody is named on is everybody's, exactly as the cards read it today"
    )


def test_a_day_nobody_is_on_a_chore_omits_the_chore_rather_than_listing_nobody(
    make_schedule_coordinator,
) -> None:
    """An override to nobody is how a parent says "skip this week"."""
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[
            chore(
                "chore_bins",
                assigned_to=[KRISHNA],
                due_days=["monday"],
                date_overrides={"2026-08-10": []},
            )
        ],
    )

    window = coordinator.schedule_window(MONDAY)

    assert named(window, 0) == {"chore_bins": [KRISHNA]}, "the first Monday is untouched"
    assert day_entry(window, 7) == {}, (
        "the overridden Monday must drop the chore entirely rather than carry an "
        "empty list - an empty list is bytes spent saying nothing"
    )


def test_a_child_who_has_left_the_household_is_dropped_from_the_days(
    make_schedule_coordinator,
) -> None:
    """Every index has to address a real row of `children`.

    Removing a child does not rewrite the `assigned_to` of every chore they
    were on, so a stale id reaching the indexer would either point at the wrong
    child or index past the end of the roster. Neither is survivable in a card.
    """
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[
            chore("chore_bins", assigned_to=[KRISHNA, "child_departed"], due_days=["monday"]),
            chore("chore_gone", assigned_to=["child_departed"], due_days=["monday"]),
        ],
    )

    window = coordinator.schedule_window(MONDAY)

    assert named(window, 0) == {"chore_bins": [KRISHNA]}, (
        "the departed child must be dropped from a chore they shared, leaving "
        "the remaining child correctly indexed"
    )
    assert "chore_gone" not in named(window, 0), (
        "and a chore left with nobody real on it drops out of the day entirely"
    )


def test_the_window_is_resolved_from_the_rules_and_not_from_stored_state(
    make_schedule_coordinator,
) -> None:
    """Asking twice must give the same answer, and asking about next year works.

    This is what makes the sensor safe to rebuild on every refresh: there is no
    cursor to advance, so a missed refresh cannot put the family out of phase.
    """
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA), child(MAYA)],
        chores=[
            chore(
                "chore_dishes",
                assigned_to=[KRISHNA, MAYA],
                rotation="week",
                rotation_order=[KRISHNA, MAYA],
            )
        ],
    )

    assert coordinator.schedule_window(MONDAY) == coordinator.schedule_window(MONDAY)
    far_future = coordinator.schedule_window(date(2031, 3, 3))
    assert far_future["days"][0], "a window years out must still resolve to somebody"
