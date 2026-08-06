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

from datetime import date, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander.const import ACTIVE_DAYS_WINDOW, SCHEDULE_WINDOW_DAYS
from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import Child, Chore, ChoreCompletion
from custom_components.choremander.schedule import weekday_name
from custom_components.choremander.sensor import StreaksSensor

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
        completions: list[ChoreCompletion] | None = None,
    ) -> ChoremanderCoordinator:
        coordinator = ChoremanderCoordinator(hass, "schedule_entry")
        coordinator.storage._data = {
            "children": [c.to_dict() for c in children or []],
            "chores": [c.to_dict() for c in chores or []],
            "rewards": [],
            "completions": [c.to_dict() for c in completions or []],
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


# ---------------------------------------------------------------------------
# The rules, published so the card can edit them
# ---------------------------------------------------------------------------
#
# The resolved days say what is true. A card that edits a rule needs to know
# what the rule *says*, and that cannot be inferred back out of the days: a
# Saturday somebody is on might be a due day or a one-off swap, and guessing
# turns "Maya covers this Saturday" into "due every Saturday" the next time the
# parent saves.


def test_the_rules_run_parallel_to_the_chores(make_schedule_coordinator) -> None:
    """Index N of `rules` is the rule for index N of `chores`."""
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[
            chore("chore_unscheduled", assigned_to=[KRISHNA]),
            chore("chore_bins", assigned_to=[KRISHNA], due_days=["monday"]),
            chore("chore_dishes", assigned_to=[KRISHNA], due_days=["friday"]),
        ],
    )

    window = coordinator.schedule_window(MONDAY)

    assert window["chores"] == ["chore_bins", "chore_dishes"]
    assert window["rules"] == [{"due_days": ["monday"]}, {"due_days": ["friday"]}], (
        "the unscheduled chore is in neither list, and the two lists must stay "
        "index-for-index or the card edits the wrong chore's rule"
    )


def test_a_rule_carries_its_values_verbatim_for_round_tripping(
    make_schedule_coordinator,
) -> None:
    """What is published is exactly what the service will be handed back.

    Indexing the child ids would silently drop a child who has left the
    household, and re-sending that pruned rule would delete them from it.
    """
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[
            chore(
                "chore_dishes",
                assigned_to=[KRISHNA],
                due_days=["saturday", "sunday"],
                rotation="occurrence",
                rotation_order=[KRISHNA, "child_departed"],
                rotation_anchor="2026-08-08",
                day_overrides={"sunday": ["child_departed"]},
                date_overrides={"2026-08-08": []},
            )
        ],
    )

    assert coordinator.schedule_window(MONDAY)["rules"] == [
        {
            "due_days": ["saturday", "sunday"],
            "rotation": "occurrence",
            "rotation_order": [KRISHNA, "child_departed"],
            "rotation_anchor": "2026-08-08",
            "day_overrides": {"sunday": ["child_departed"]},
            "date_overrides": {"2026-08-08": []},
        }
    ], "every stored value must survive the round trip, including ids off the roster"


def test_a_rule_omits_the_fields_sitting_at_their_default(
    make_schedule_coordinator,
) -> None:
    """A rule that only sets due days is one key, not six."""
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[chore("chore_bins", assigned_to=[KRISHNA], due_days=["monday"])],
    )

    assert coordinator.schedule_window(MONDAY)["rules"] == [{"due_days": ["monday"]}], (
        "rotation 'none', empty orders and empty override maps are what a "
        "reader assumes when a key is absent, so spending bytes on them is waste"
    )


def test_an_unassigned_chores_rule_does_not_freeze_the_household_into_it(
    make_schedule_coordinator,
) -> None:
    """Standing the roster in for "anyone" is a resolution-time convenience.

    Publishing the substituted roster would mean the card writing the rule
    back turns "anyone" into today's list of children, and the chore stops
    following the family as it changes.
    """
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA), child(MAYA)],
        chores=[chore("chore_tidy", due_days=["monday"])],
    )

    window = coordinator.schedule_window(MONDAY)

    assert window["rules"] == [{"due_days": ["monday"]}], (
        "the rule must not have grown an assigned_to or a rotation_order it never had"
    )
    assert named(window, 0) == {"chore_tidy": [KRISHNA, MAYA]}, (
        "while the resolved day still hands it to the whole household"
    )


# ---------------------------------------------------------------------------
# Rest days - what a schedule does to a streak
# ---------------------------------------------------------------------------
#
# `compute_streaks` treats a day the child had nothing scheduled as neutral,
# and the set of those days is derived here. These tests cover the derivation
# and the publishing; `test_streaks.py` covers what the neutrality then does to
# a run. Unlike the window above, this reads the real clock - `_rest_days` is
# anchored on today - so every expectation below is built from `today` rather
# than from a literal date.


def rest_days_for(coordinator: ChoremanderCoordinator, child_id: str) -> set[str]:
    """The child's derived rest days, as the ISO strings the sensor publishes."""
    summary = coordinator._build_streak_summary(
        coordinator.storage.get_children(), coordinator.storage.get_completions()
    )
    return set(summary[child_id]["rest_days"])


def test_an_unscheduled_household_has_no_rest_days_at_all(
    make_schedule_coordinator,
) -> None:
    """The property that keeps every existing streak exactly as it was.

    A chore with no rule falls every day, so nobody is ever off. If this were
    not true, shipping the feature would hand a rest day to every child in
    every install and quietly change streaks nobody asked to change.
    """
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA), child(MAYA)],
        chores=[chore("chore_bed", assigned_to=[KRISHNA]), chore("chore_dishes")],
    )

    assert rest_days_for(coordinator, KRISHNA) == set()
    assert rest_days_for(coordinator, MAYA) == set(), (
        "Maya is on the unassigned chore, which falls every day, so she has no day off either"
    )


def test_a_child_with_no_chores_at_all_is_not_resting(
    make_schedule_coordinator,
) -> None:
    """ "Nothing has happened yet" is not the same as "you had the day off".

    A brand-new household has no chores, and a sibling can have every chore
    assigned away from them. Calling those days off would fill the streak strip
    with day-off dots for a family that has never opened this feature - the
    exact opposite of the feature being invisible until it is used.
    """
    empty = make_schedule_coordinator(children=[child(KRISHNA)])
    assert rest_days_for(empty, KRISHNA) == set(), "no chores exist, so nobody is resting"

    others_only = make_schedule_coordinator(
        children=[child(KRISHNA), child(MAYA)],
        chores=[chore("chore_bins", assigned_to=[MAYA], due_days=["monday"])],
    )
    assert rest_days_for(others_only, KRISHNA) == set(), (
        "Krishna is on nothing anywhere in the window - that is a child with no "
        "chores, not a child with thirty days off"
    )
    assert rest_days_for(others_only, MAYA), (
        "while Maya, who is on something, does get the days she is not on it"
    )


def test_the_days_a_chore_does_not_fall_become_rest_days(
    make_schedule_coordinator,
) -> None:
    """A chore due one day a week leaves the child off on the other six."""
    today = dt_util.now().date()
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[chore("chore_bins", assigned_to=[KRISHNA], due_days=[weekday_name(today)])],
    )

    rest = rest_days_for(coordinator, KRISHNA)
    window = [today - timedelta(days=offset) for offset in range(ACTIVE_DAYS_WINDOW)]
    expected = {day.isoformat() for day in window if day.weekday() != today.weekday()}

    assert rest == expected, (
        "every day of the 30-day window except the ones sharing today's weekday must be a rest day"
    )
    assert today.isoformat() not in rest, "the day the chore is actually due is not a day off"


def test_a_day_off_the_child_worked_anyway_is_not_a_rest_day(
    make_schedule_coordinator,
) -> None:
    """Doing a chore on your day off is doing a chore.

    Leaving the day in both sets would publish it to the card as a muted "day
    off" dot with a real completion sitting behind it, which reads as the app
    having lost the work.
    """
    today = dt_util.now().date()
    yesterday = today - timedelta(days=1)
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[chore("chore_bins", assigned_to=[KRISHNA], due_days=[weekday_name(today)])],
        completions=[
            ChoreCompletion(
                chore_id="chore_bins",
                child_id=KRISHNA,
                completed_at=dt_util.start_of_local_day(yesterday) + timedelta(hours=9),
            )
        ],
    )

    rest = rest_days_for(coordinator, KRISHNA)

    assert yesterday.isoformat() not in rest, (
        "yesterday was not a scheduled day, but the child worked it, so it is an "
        "active day and not a rest day"
    )
    assert (today - timedelta(days=2)).isoformat() in rest, (
        "the day before that had nothing scheduled and nothing done, and stays a rest day"
    )


async def test_the_streaks_sensor_publishes_rest_days_beside_active_days(
    hass: HomeAssistant, make_schedule_coordinator
) -> None:
    """The card cannot draw a day off it is never told about.

    Without this the strip renders a scheduled day off as a gap - visually
    identical to a miss - directly beside a streak number that counted it as
    neither. The two would contradict each other on screen.
    """
    today = dt_util.now().date()
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[chore("chore_bins", assigned_to=[KRISHNA], due_days=[weekday_name(today)])],
    )
    await coordinator.async_refresh()

    entry = MockConfigEntry(domain="choremander", entry_id="schedule_entry")
    attributes = StreaksSensor(coordinator, entry).extra_state_attributes
    row = attributes["children"][0]

    assert "rest_days" in row, "the streak card reads attributes.children[].rest_days"
    assert set(row["rest_days"]) == rest_days_for(coordinator, KRISHNA), (
        "and it must publish the same set the streak numbers were derived from, "
        "or the strip and the count disagree"
    )
    assert row["rest_days"] == sorted(row["rest_days"]), (
        "published in date order, so the card can walk it without sorting"
    )


async def test_the_streaks_sensor_publishes_no_rest_days_for_an_unscheduled_household(
    hass: HomeAssistant, make_schedule_coordinator
) -> None:
    """The key appears for everyone, empty until a family schedules something."""
    coordinator = make_schedule_coordinator(
        children=[child(KRISHNA)],
        chores=[chore("chore_bed", assigned_to=[KRISHNA])],
    )
    await coordinator.async_refresh()

    entry = MockConfigEntry(domain="choremander", entry_id="schedule_entry")
    attributes = StreaksSensor(coordinator, entry).extra_state_attributes

    assert attributes["children"][0]["rest_days"] == [], (
        "an always-present key that is empty is what lets the card branch on it "
        "without checking whether the integration is new enough to send it"
    )
