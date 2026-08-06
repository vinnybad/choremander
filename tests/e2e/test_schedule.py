"""Day-of-week scheduling, against a live Home Assistant.

`tests/unit/test_schedule.py` proves the rota arithmetic: anchors in the
future, cycles crossing a year, a rotation order longer than the due days. It
proves all of it against a calendar handed in by the test, with no storage, no
coordinator and no state machine in the way.

What it cannot prove is that any of that reaches a parent. Everything between
`assignees_on` and the card is exactly where the product's previous derived
features failed - a streak that was computed correctly, stored, and then
published by a sensor that never read it. So these tests assert only on what
the entities publish and on what the service writes back, and never on a Python
object.

What is worth checking here, in the order the design's Testing section asks for
it:

* the rules the fixture ships survive storage and come back out shaped as the
  card reads them;
* a chore is in a day's entry on its due day and absent from it otherwise -
  including the case that matters most, a chore with no rule, which is absent
  from the payload entirely and must still be read as "on every day";
* `set_chore_schedule` round-trips, leaving fields it was not given alone;
* and a scheduled day off reaches the streak sensor as a rest day, because
  losing a streak on a day the family was told to rest is the failure the whole
  rest-day concept exists to prevent.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, timedelta
from pathlib import Path
import subprocess
from typing import Any

import pytest

from .ha_client import HAClient

# `e2e` is declared at module level rather than added during collection so that
# `-m "not e2e"` reliably deselects the whole file without a container running.
pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]

SCHEDULE = "sensor.choremander_schedule"
STREAKS = "sensor.choremander_streaks"

# Ids rather than display names, because a schedule is written and published in
# ids: the sensor's `rules` carry the stored child ids verbatim so that a card
# can hand them straight back to the service.
KRISHNA = "child_krishna"
RADHA = "child_radha"
MEERA = "child_meera"

# The three chores the fixture puts on a schedule. Every one of them is Meera's,
# and the dishes are hers and Krishna's evening card only: a chore that is only
# on some days is invisible on the others, and the browser specs pin what
# Krishna's morning card and Radha's evening card list on any day of the week.
FISH = "chore_fish"
BINS = "chore_bins"
DISHES = "chore_dishes"

BED = "chore_bed"

# The chores the fixture leaves without any rule at all. Every one of them is
# on every day for whoever it is assigned to, which is what every installed
# household looks like today.
UNSCHEDULED = (
    BED,
    "chore_teeth_am",
    "chore_feed_dog",
    "chore_water_plants",
    "chore_homework",
    "chore_practice_piano",
    "chore_teeth_pm",
    "chore_tidy_room",
)

# Feed the Fish is due on all of these and on no Sunday, which is what leaves
# Meera - who is on no unscheduled chore at all - a day off every week.
WORKING_WEEK = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")

# Every scheduling field at its default. Sending a field replaces its stored
# value wholesale, so spelling all six out is what makes a restore a restore
# rather than a partial merge onto whatever the test left behind.
CLEARED_RULE: dict[str, Any] = {
    "due_days": [],
    "rotation": "none",
    "rotation_order": [],
    "rotation_anchor": "",
    "day_overrides": {},
    "date_overrides": {},
}


@pytest.fixture(scope="module", autouse=True)
def fresh_schedules() -> None:
    """Reseed before this module, on top of the session-wide reset.

    The assertions below name the fixture's own three rules and the exact set of
    chores that carry none, so a module that ran earlier and left a rule behind
    would change every count here. The browser specs reseed per file for the
    same reason.
    """
    seed = REPO_ROOT / "dev" / "seed.sh"
    if not seed.exists():
        pytest.skip(f"{seed} is missing; cannot reach a known starting state.")

    result = subprocess.run([str(seed)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        pytest.skip(f"{seed} exited {result.returncode}:\n{result.stdout}\n{result.stderr}")


@pytest.fixture(autouse=True)
def restore_schedules(ha: HAClient) -> Generator[None]:
    """Put every rule back the way the test found it.

    Schedules are persistent and there is no service that resets one, so
    without this a test that puts every chore on a Monday-to-Saturday week
    leaves the container in that state for every test after it and for anyone
    who opens the dev dashboard afterwards. Restored through the same service
    the tests use rather than by reseeding, because reseeding stops and
    restarts the container and this runs after every test in the file.
    """
    before = _rules(ha)
    yield
    after = _rules(ha)
    for chore_id in set(before) | set(after):
        if before.get(chore_id, {}) != after.get(chore_id, {}):
            _write_rule(ha, chore_id, before.get(chore_id, {}))


# ---------------------------------------------------------------------------
# Reading the published window
# ---------------------------------------------------------------------------


def _window(ha: HAClient) -> dict[str, Any]:
    return ha.attributes(SCHEDULE)


def _rules_of(window: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every published rule, keyed by chore id.

    `rules` runs parallel to `chores` index for index; zipping them here is the
    only place this file relies on that, so a payload that ever broke the
    pairing fails loudly rather than silently attributing one chore's rule to
    another.
    """
    assert len(window["rules"]) == len(window["chores"]), (
        f"{SCHEDULE} published {len(window['chores'])} chores but "
        f"{len(window['rules'])} rules; the card reads them index for index"
    )
    return dict(zip(window["chores"], window["rules"], strict=True))


def _rules(ha: HAClient) -> dict[str, dict[str, Any]]:
    return _rules_of(_window(ha))


def _write_rule(ha: HAClient, chore_id: str, rule: dict[str, Any]) -> None:
    """Set a chore's whole rule, defaults included."""
    ha.call_service("choremander", "set_chore_schedule", chore_id=chore_id, **(CLEARED_RULE | rule))


def _day(window: dict[str, Any], offset: int) -> date:
    """The date of day `offset` of the window.

    Derived from the sensor's own `window_start` rather than from the test
    machine's clock: the container runs in its own timezone, and a window that
    starts on Monday there can be Sunday here.
    """
    return date.fromisoformat(window["window_start"]) + timedelta(days=offset)


def _assignees(window: dict[str, Any], chore_id: str, offset: int) -> list[str]:
    """The child ids on `chore_id` on day `offset`, resolved out of the indices.

    Raises if the chore carries no rule at all. That case is not "nobody" - it
    is "everyone it is assigned to, every day" - and a helper that returned an
    empty list for it would let a test claim a chore was off when the payload
    was only staying quiet about it.
    """
    assert chore_id in window["chores"], (
        f"{chore_id} carries no rule, so it is absent from the window entirely. "
        "That reads as on every day, not as nobody."
    )
    index = window["chores"].index(chore_id)
    return [window["children"][who] for who in window["days"][offset].get(str(index), [])]


def _offsets_on(window: dict[str, Any], chore_id: str) -> list[int]:
    """Which days of the window anybody is on `chore_id`."""
    return [offset for offset in range(len(window["days"])) if _assignees(window, chore_id, offset)]


# ---------------------------------------------------------------------------
# The seeded rules reach the sensor intact
# ---------------------------------------------------------------------------


def test_the_schedule_sensor_publishes_a_window_the_card_can_read(ha: HAClient) -> None:
    """The card refuses to draw at all without these, so they have to ship.

    `window_start`, `days`, `chores` and `rules` are the card's `requiredData`:
    missing any one of them, it renders a configuration error instead of a
    grid. The other two are read on every render, so a payload without them
    would draw a fortnight labelled with the wrong dates.
    """
    window = _window(ha)

    for attribute in ("window_start", "window_days", "children", "chores", "rules", "days"):
        assert attribute in window, f"the schedule card reads attributes.{attribute}"

    assert date.fromisoformat(window["window_start"]).weekday() == 0, (
        f"the window must start on a Monday; got {window['window_start']}"
    )
    assert window["window_days"] == 14
    assert len(window["days"]) == 14, "every published day needs an entry, empty or not"
    assert window["children"] == [KRISHNA, RADHA, MEERA], (
        "the day entries index into this roster, so its order is a contract"
    )


def test_the_seeded_rules_survive_the_round_trip_through_storage(ha: HAClient) -> None:
    """Published verbatim - real ids and weekday names, not resolved indices.

    A card editing a rule hands these values straight back to the service, so
    anything lost or translated on the way out here becomes a rule the parent
    never wrote the next time they touch the chore.
    """
    rules = _rules(ha)

    assert rules[FISH] == {"due_days": list(WORKING_WEEK)}, (
        "Feed the Fish is Meera's on every day but Sunday and sets nothing "
        f"else; every field at its default is omitted. Got {rules[FISH]}"
    )
    assert rules[BINS] == {"due_days": ["monday", "thursday"]}, (
        "Take Out the Bins is the seed's 'bigger chores only on certain days' "
        f"case: two weekdays and no other field. Got {rules[BINS]}"
    )
    assert rules[DISHES] == {
        "due_days": ["wednesday", "saturday"],
        "rotation": "occurrence",
        "rotation_order": [KRISHNA, MEERA],
    }, (
        "Wash the Dishes falls on Wednesdays and Saturdays with Krishna and "
        f"Meera taking turns, and carries no anchor or overrides. Got {rules[DISHES]}"
    )


def test_a_chore_with_no_rule_is_left_out_rather_than_switched_off(ha: HAClient) -> None:
    """The one reading that would break every existing install if inverted.

    Eight of the eleven seeded chores carry no rule. They are omitted from this
    payload to keep it under the recorder's 16KiB attribute ceiling, and the
    card reads a missing chore exactly as it behaved before scheduling existed:
    on, every day, for whoever it is assigned to. The overview sensor still
    lists them, which is where the card gets them from.
    """
    window = _window(ha)
    assert sorted(window["chores"]) == sorted([FISH, BINS, DISHES]), (
        f"only chores carrying a rule belong in the schedule window; got {window['chores']}"
    )

    known = {chore["id"] for chore in ha.overview()["chores"]}
    for chore_id in UNSCHEDULED:
        assert chore_id not in window["chores"]
        assert chore_id in known, (
            f"{chore_id} is missing from both sensors, so no card can draw it "
            "at all - being left out of the schedule must not delete a chore"
        )


# ---------------------------------------------------------------------------
# A chore is on its due day and absent otherwise
# ---------------------------------------------------------------------------


def test_a_chore_is_in_a_days_entry_on_its_due_day_and_absent_otherwise(
    ha: HAClient,
) -> None:
    """Both weeks, so this is the rule repeating rather than one lucky week."""
    window = _window(ha)

    for offset in range(14):
        day = _day(window, offset)
        on = _assignees(window, BINS, offset)
        if day.weekday() in (0, 3):  # Monday, Thursday
            assert on == [MEERA], (
                f"Take Out the Bins is due on {day:%A} {day} and is Meera's, so "
                f"she is the entry for it; got {on}"
            )
        else:
            assert on == [], (
                f"Take Out the Bins is not due on {day:%A} {day}, so it must be "
                f"omitted from that day rather than carried with {on} on it"
            )


def test_a_rotation_hands_each_occurrence_to_exactly_one_child(ha: HAClient) -> None:
    """The second reading of the feature: the children take turns.

    Wash the Dishes is assigned to Krishna and Meera both, so without a
    rotation the two of them would be on it together every Wednesday and
    Saturday. With one, each due day names one child and the next due day names
    the other - and, because the cycle is counted from a fixed anchor rather
    than from a stored cursor, the same weekday lands on the same child in both
    published weeks.
    """
    window = _window(ha)

    turns: dict[int, set[str]] = {}
    for offset in _offsets_on(window, DISHES):
        day = _day(window, offset)
        on = _assignees(window, DISHES, offset)
        assert day.weekday() in (2, 5), f"Wash the Dishes is not due on {day:%A}"
        assert len(on) == 1, f"a rotation names one child per occurrence; {day} got {on}"
        turns.setdefault(day.weekday(), set()).update(on)

    assert len(turns) == 2, "both due weekdays fall inside a fortnight"
    wednesdays, saturdays = turns[2], turns[5]
    assert len(wednesdays) == 1 and len(saturdays) == 1, (
        f"each weekday belongs to one child across the window; got {turns}"
    )
    assert wednesdays != saturdays, (
        f"consecutive occurrences must change hands, but both went to {wednesdays}"
    )


# ---------------------------------------------------------------------------
# Writing a rule back
# ---------------------------------------------------------------------------


def test_set_chore_schedule_round_trips_a_new_rule(ha: HAClient) -> None:
    """A chore with no rule can be given one, and the window follows."""
    ha.call_service("choremander", "set_chore_schedule", chore_id=BED, due_days=["sunday"])
    ha.wait_for(
        SCHEDULE,
        lambda state: BED in state["attributes"]["chores"],
        description="Make Bed to join the schedule window",
    )

    window = _window(ha)
    assert _rules_of(window)[BED] == {"due_days": ["sunday"]}, (
        "the rule must come back as it was sent - a card writing an override "
        "onto it next has nothing else to merge onto"
    )

    sundays = [offset for offset in range(14) if _day(window, offset).weekday() == 6]
    assert _offsets_on(window, BED) == sundays, (
        f"Make Bed is due on Sundays, which are days {sundays} of this window"
    )
    for offset in sundays:
        assert sorted(_assignees(window, BED, offset)) == sorted([KRISHNA, RADHA]), (
            "Make Bed is Krishna's and Radha's both - putting it on a day must "
            "not quietly narrow that to one child"
        )


def test_a_field_the_call_leaves_out_is_left_alone(ha: HAClient) -> None:
    """The property the whole card depends on being true.

    The schedule card edits one row of the grid at a time and sends only the
    field it changed. If an omitted field read as "clear this", moving a single
    Saturday would wipe the rotation the parent set last week - silently, and
    with the grid redrawing as though that was what they asked for.
    """
    ha.call_service(
        "choremander",
        "set_chore_schedule",
        chore_id=DISHES,
        day_overrides={"saturday": [KRISHNA]},
    )
    ha.wait_for(
        SCHEDULE,
        lambda state: "day_overrides" in _rules_of(state["attributes"]).get(DISHES, {}),
        description="the Saturday override to reach the schedule sensor",
    )

    rule = _rules(ha)[DISHES]
    assert rule["day_overrides"] == {"saturday": [KRISHNA]}
    assert rule["due_days"] == ["wednesday", "saturday"], "the due days were not part of the call"
    assert rule["rotation"] == "occurrence", "nor was the rotation"
    assert rule["rotation_order"] == [KRISHNA, MEERA]

    window = _window(ha)
    for offset in _offsets_on(window, DISHES):
        if _day(window, offset).weekday() == 5:
            assert _assignees(window, DISHES, offset) == [KRISHNA], (
                "an override for a weekday wins over the rotation on that day"
            )


# ---------------------------------------------------------------------------
# A scheduled day off is not a broken streak
# ---------------------------------------------------------------------------


def test_the_seeded_day_off_is_published_as_a_rest_day(ha: HAClient) -> None:
    """Nobody should lose a streak on a day the family took off.

    A rest day is a day the child was on no chore at all, which is why every
    one of Meera's three chores carries a rule and none of them falls on a
    Sunday. A single unscheduled chore anywhere in her list would land on every
    day of the week, Sundays included, and there would be no day off left to
    publish - which is exactly the state the fixture was in before, and why
    neither this nor the card's "day off" state was reachable from it.

    Asserted straight off the seed rather than by scheduling the whole house
    from the test: a rest day a family can actually arrive at by using the
    feature is the thing worth proving, and the setup is then the fixture the
    browser specs share rather than a state only this file ever sees.
    """
    rows = {row["name"]: row for row in ha.attributes(STREAKS)["children"]}
    assert set(rows) == {"Krishna", "Radha", "Meera"}, (
        f"the streak sensor publishes a row per child; got {sorted(rows)}"
    )

    meera = rows["Meera"]
    rest = [date.fromisoformat(day) for day in meera["rest_days"]]
    assert {day.weekday() for day in rest} == {6}, (
        "Meera is on the fish six days a week, the bins on Mondays and "
        "Thursdays and the dishes on her Saturdays, so Sunday is her only day "
        f"off; got {meera['rest_days']}"
    )
    # The strip the card draws is thirty days long and holds four whole weeks
    # whichever day it starts on.
    assert len(rest) >= 4, f"Meera has only {len(rest)} days off in a thirty-day window"
    assert not set(meera["rest_days"]) & set(meera["active_days"]), (
        "a day the child worked anyway is a day they worked, and must not "
        "also be drawn as a day off"
    )

    for name in ("Krishna", "Radha"):
        assert rows[name]["rest_days"] == [], (
            f"{name} is on unscheduled chores, which fall on every day, so no "
            f"day of theirs is a day off; got {rows[name]['rest_days']}"
        )
