"""The points ledger and derived streaks, against a live Home Assistant.

The unit suite proves the ledger reconciles and the streak arithmetic is right.
What it cannot prove is that any of it survives the round trip through Home
Assistant's storage, its coordinator refresh and its state machine - which is
where the previous versions of both features failed:

* `current_streak` was stored, published by two sensors, and written by nothing,
  so it read zero forever. Only a live check catches "the number the sensor
  actually publishes".
* The reason given for a manual adjustment was accepted by the service schema
  and then dropped, so nothing downstream could ever show it.

These tests therefore assert on published entity state, never on Python objects.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from .environment import environment_unavailable
from .ha_client import HAClient

# `e2e` is declared at module level rather than added during collection so that
# `-m "not e2e"` reliably deselects the whole file without a container running.
pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("restore_points")]

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def fresh_ledger() -> None:
    """Reseed before this module, on top of the session-wide reset.

    Two reasons, both specific to what is asserted here. Chores carry a daily
    limit, so a chore another module already completed today cannot be completed
    again - and the reconciliation test at the bottom needs the ledger to start
    empty against known balances, which stops being true once another module's
    activity has filled it. The browser specs reseed per file for the same
    reason.
    """
    seed = REPO_ROOT / "dev" / "seed.sh"
    if not seed.exists():
        environment_unavailable(f"{seed} is missing; cannot reach a known starting state.")

    result = subprocess.run([str(seed)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        environment_unavailable(
            f"{seed} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )


ACTIVITY = "sensor.choremander_activity"
STREAKS = "sensor.choremander_streaks"

KRISHNA = "Krishna"
RADHA = "Radha"


def _events(ha: HAClient) -> list[dict]:
    return ha.attributes(ACTIVITY).get("events", [])


def _streak_row(ha: HAClient, child_name: str) -> dict:
    children = ha.attributes(STREAKS).get("children", [])
    return next(c for c in children if c["name"] == child_name)


def _complete(ha: HAClient, chore_name: str, child_name: str) -> None:
    ha.call_service(
        "choremander",
        "complete_chore",
        chore_id=ha.chore(chore_name)["id"],
        child_id=ha.child(child_name)["id"],
    )


def _approve(ha: HAClient, chore_name: str, child_name: str) -> None:
    chore_id = ha.chore(chore_name)["id"]
    child_id = ha.child(child_name)["id"]
    pending = ha.attributes("sensor.pending_approvals").get("chore_completions", [])
    completion = next(c for c in pending if c["chore_id"] == chore_id and c["child_id"] == child_id)
    ha.call_service("choremander", "approve_chore", completion_id=completion["completion_id"])


# ---------------------------------------------------------------------------
# The sensors exist and are shaped as the cards expect
# ---------------------------------------------------------------------------


def test_the_activity_and_streak_sensors_are_published(ha: HAClient) -> None:
    """Both cards read a dedicated entity rather than the overview sensor."""
    activity = ha.attributes(ACTIVITY)
    assert "events" in activity, "the activity card reads attributes.events"
    assert "points_icon" in activity and "points_name" in activity

    streaks = ha.attributes(STREAKS)
    assert streaks["children"], "the streak card reads attributes.children"
    for row in streaks["children"]:
        assert {"current_streak", "best_streak", "active_days"} <= set(row)


def test_the_overview_sensor_no_longer_carries_the_streak_payload(ha: HAClient) -> None:
    """It is the closest sensor to the recorder's 16KiB attribute ceiling.

    Keeping streak data off it is not tidiness - a household of five children
    leaves it a few hundred bytes short of the limit, past which Home Assistant
    silently stops recording the entity at all.
    """
    for row in ha.overview()["children"]:
        assert "active_days" not in row
        assert "current_streak" not in row


# ---------------------------------------------------------------------------
# Every balance change is accounted for
# ---------------------------------------------------------------------------


def test_an_approved_chore_moves_from_pending_to_credited(ha: HAClient) -> None:
    """One chore instance produces one row, whichever state it is in."""
    _complete(ha, "Do Homework", KRISHNA)
    ha.wait_for(
        ACTIVITY,
        lambda _: any(e["event_type"] == "chore_pending" for e in _events(ha)),
        description="the pending chore to reach the activity feed",
    )

    pending = [e for e in _events(ha) if e["event_type"] == "chore_pending"]
    assert len(pending) == 1
    assert pending[0]["points"] == 0, "nothing has been credited yet"
    assert pending[0]["pending_points"] == 5, "but the card can say what is at stake"
    assert pending[0]["chore_name"] == "Do Homework"

    _approve(ha, "Do Homework", KRISHNA)
    ha.wait_for(
        ACTIVITY,
        lambda _: any(e["event_type"] == "chore_approved" for e in _events(ha)),
        description="the approval to reach the activity feed",
    )

    homework = [e for e in _events(ha) if e.get("chore_name") == "Do Homework"]
    assert len(homework) == 1, (
        "the pending row must be replaced by the credit, not joined by it - "
        f"got {[e['event_type'] for e in homework]}"
    )
    assert homework[0]["event_type"] == "chore_approved"
    assert homework[0]["points"] == 5


def test_the_reason_for_an_adjustment_reaches_the_feed(ha: HAClient) -> None:
    """The service accepted a reason and discarded it before this existed."""
    ha.call_service(
        "choremander",
        "add_points",
        child_id=ha.child(RADHA)["id"],
        points=9,
        reason="Tidied the whole playroom",
    )
    ha.wait_for(
        ACTIVITY,
        lambda _: any(e.get("reason") == "Tidied the whole playroom" for e in _events(ha)),
        description="the bonus to reach the activity feed with its reason",
    )

    bonus = next(e for e in _events(ha) if e.get("reason") == "Tidied the whole playroom")
    assert bonus["event_type"] == "points_added"
    assert bonus["points"] == 9
    assert bonus["child_name"] == RADHA


def test_a_penalty_is_recorded_as_a_negative_movement(ha: HAClient) -> None:
    ha.call_service(
        "choremander",
        "remove_points",
        child_id=ha.child(RADHA)["id"],
        points=4,
        reason="Left the bathroom in a state",
    )
    ha.wait_for(
        ACTIVITY,
        lambda _: any(e["event_type"] == "points_removed" for e in _events(ha)),
        description="the penalty to reach the activity feed",
    )

    penalty = next(e for e in _events(ha) if e["event_type"] == "points_removed")
    assert penalty["points"] == -4, "a debit is signed, so a card never has to guess"
    assert penalty["reason"] == "Left the bathroom in a state"


def test_the_feed_is_newest_first(ha: HAClient) -> None:
    """The card renders in the order it receives, so the order is a contract."""
    timestamps = [e["occurred_at"] for e in _events(ha)]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"the feed must arrive newest-first; got {timestamps}"
    )


def test_events_never_name_a_chore_or_reward_that_was_deleted(ha: HAClient) -> None:
    """A missing name is omitted rather than sent as an empty string.

    The card falls back to generic wording when the key is absent, so sending
    an empty string would render "completed" followed by nothing at all.
    """
    for event in _events(ha):
        assert event.get("chore_name") != ""
        assert event.get("reward_name") != ""
        assert event.get("reason") != ""


# ---------------------------------------------------------------------------
# Streaks are derived, not stored
# ---------------------------------------------------------------------------


def test_completing_a_chore_starts_a_streak_the_sensor_actually_publishes(
    ha: HAClient,
) -> None:
    """The regression that matters: this number could only ever read zero."""
    _complete(ha, "Water the Plants", RADHA)

    ha.wait_for(
        STREAKS,
        lambda _: _streak_row(ha, RADHA)["current_streak"] >= 1,
        description=f"{RADHA}'s streak to start",
    )

    row = _streak_row(ha, RADHA)
    assert row["current_streak"] == 1
    assert row["best_streak"] >= 1, "the best streak is never behind the current one"
    assert len(row["active_days"]) == 1, "one day of activity, however many chores"


def test_the_streak_number_and_the_dot_strip_come_from_the_same_place(
    ha: HAClient,
) -> None:
    """They are drawn separately by the card, so they must agree at the source."""
    for row in ha.attributes(STREAKS)["children"]:
        if row["current_streak"] > 0:
            assert row["active_days"], f"{row['name']} has a live streak but no active days to draw"
        assert row["best_streak"] >= row["current_streak"]


def test_a_manual_bonus_is_not_counted_as_a_chore(ha: HAClient) -> None:
    """The lifetime chore count drives the streak card's milestones."""
    before = _streak_row(ha, KRISHNA)["total_chores_completed"]

    ha.call_service(
        "choremander",
        "add_points",
        child_id=ha.child(KRISHNA)["id"],
        points=6,
        reason="Pocket money",
    )
    ha.wait_for(
        ACTIVITY,
        lambda _: any(e.get("reason") == "Pocket money" for e in _events(ha)),
        description="the bonus to be recorded",
    )

    after = _streak_row(ha, KRISHNA)
    assert after["total_chores_completed"] == before, "handing out points is not doing a chore"
    assert after["total_points_earned"] >= 6, "but it does count as points earned"


# ---------------------------------------------------------------------------
# The ledger agrees with the balances it explains
# ---------------------------------------------------------------------------


def test_the_feed_accounts_for_every_movement_since_the_seed(ha: HAClient) -> None:
    """Sum the published events and the arithmetic must land on the balance.

    The fixture starts from a known balance with an empty ledger, and every test
    above moved points only through a service. So for each child the seeded
    balance plus the sum of their events is exactly what the overview reports -
    unless something moved points without accounting for it.
    """
    seeded = {KRISHNA: 25, RADHA: 15}

    movements: dict[str, int] = {}
    for event in _events(ha):
        if event["event_type"] == "chore_pending":
            continue  # nothing credited yet, and it carries points 0 anyway
        movements[event["child_name"]] = movements.get(event["child_name"], 0) + event["points"]

    for name, start in seeded.items():
        expected = start + movements.get(name, 0)
        assert ha.points(name) == expected, (
            f"{name}'s balance is {ha.points(name)} but the ledger explains "
            f"{start} + {movements.get(name, 0)} = {expected}. Some path moved "
            "points without recording why."
        )
