"""End-to-end chore journeys driven against a live Home Assistant container.

Each test exercises a path a real family would take, through the same service
calls the Lovelace cards make, and asserts on the state Home Assistant actually
publishes. Nothing here mocks any part of the integration.
"""

from __future__ import annotations

import contextlib

import pytest

from .ha_client import HAClient, HomeAssistantError

# `e2e` is declared at module level rather than added during collection so that
# `-m "not e2e"` reliably deselects the whole file without a container running.
pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("restore_points")]

# Seeded fixture data - see dev/config/.storage/choremander.storage.*
KRISHNA = "Krishna"
RADHA = "Radha"


def _complete(ha: HAClient, chore_name: str, child_name: str) -> None:
    ha.call_service(
        "choremander",
        "complete_chore",
        chore_id=ha.chore(chore_name)["id"],
        child_id=ha.child(child_name)["id"],
    )


def _pending_completions(ha: HAClient) -> list[dict]:
    """Pending chore completions, newest-first ordering not guaranteed."""
    return ha.attributes("sensor.pending_approvals").get("chore_completions", [])


def _pending_for(ha: HAClient, chore_name: str) -> list[dict]:
    chore_id = ha.chore(chore_name)["id"]
    return [c for c in _pending_completions(ha) if c.get("chore_id") == chore_id]


def test_completing_an_unapproved_chore_awards_points_immediately(ha: HAClient) -> None:
    """'Make Bed' is worth 2 points and needs no approval, so the balance moves at once."""
    before = ha.points(KRISHNA)

    _complete(ha, "Make Bed", KRISHNA)

    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(KRISHNA) == before + 2,
        description=f"{KRISHNA}'s balance to rise from {before} to {before + 2}",
    )


def test_chore_requiring_approval_withholds_points_until_approved(ha: HAClient) -> None:
    """'Do Homework' is worth 5 points but must be approved first."""
    before = ha.points(KRISHNA)

    _complete(ha, "Do Homework", KRISHNA)

    # Points must NOT move yet - this is the whole point of the approval gate.
    ha.wait_for(
        "sensor.pending_approvals",
        lambda s: int(s["state"]) >= 1,
        description="a pending approval to appear",
    )
    assert ha.points(KRISHNA) == before, "Points were awarded before a parent approved the chore"

    homework = _pending_for(ha, "Do Homework")
    assert homework, (
        f"Completed chore is missing from pending approvals: {_pending_completions(ha)}"
    )

    ha.call_service("choremander", "approve_chore", completion_id=homework[0]["completion_id"])

    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(KRISHNA) == before + 5,
        description=f"{KRISHNA}'s balance to rise to {before + 5} after approval",
    )


def test_rejecting_a_chore_awards_nothing(ha: HAClient) -> None:
    """A rejected chore clears the queue without paying out."""
    before = ha.points(RADHA)

    _complete(ha, "Practice Piano", RADHA)
    ha.wait_for(
        "sensor.pending_approvals",
        lambda s: int(s["state"]) >= 1,
        description="a pending approval to appear",
    )

    pending = _pending_for(ha, "Practice Piano")
    assert pending, "Practice Piano did not reach the approval queue"

    ha.call_service("choremander", "reject_chore", completion_id=pending[0]["completion_id"])

    ha.wait_for(
        "sensor.pending_approvals",
        lambda _: not _pending_for(ha, "Practice Piano"),
        description="the rejected chore to leave the queue",
    )
    assert ha.points(RADHA) == before, "A rejected chore paid out points"


def test_daily_limit_blocks_a_second_completion(ha: HAClient) -> None:
    """Every seeded chore has daily_limit 1, so a repeat must not pay twice."""
    _complete(ha, "Brush Teeth (Morning)", RADHA)
    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(RADHA) >= 0,
        description="the first completion to settle",
    )
    after_first = ha.points(RADHA)

    # A second attempt on the same day either errors or is silently ignored;
    # either is acceptable, but it must not award points again.
    with contextlib.suppress(HomeAssistantError):
        _complete(ha, "Brush Teeth (Morning)", RADHA)

    assert ha.points(RADHA) == after_first, "A chore with daily_limit=1 paid out twice in one day"


def test_claiming_a_reward_deducts_its_cost(ha: HAClient) -> None:
    """Krishna can afford the 50-point video game once topped up."""
    reward = ha.reward("New Video Game")
    cost = int(reward["cost"])
    krishna_id = ha.child(KRISHNA)["id"]

    ha.set_points(krishna_id, cost, ha.points(KRISHNA))
    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(KRISHNA) == cost,
        description=f"{KRISHNA} to be topped up to {cost}",
    )

    ha.call_service("choremander", "claim_reward", reward_id=reward["id"], child_id=krishna_id)

    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(KRISHNA) == 0,
        description=f"the {cost}-point cost to be deducted",
    )


def test_claiming_an_unaffordable_reward_does_not_deduct(ha: HAClient) -> None:
    """A claim beyond the balance must leave the balance untouched."""
    reward = ha.reward("Trip to the Zoo")  # 150 points
    radha_id = ha.child(RADHA)["id"]

    ha.set_points(radha_id, 5, ha.points(RADHA))
    ha.wait_for(ha.OVERVIEW, lambda _: ha.points(RADHA) == 5, description="Radha to be set to 5")

    with contextlib.suppress(HomeAssistantError):
        ha.call_service("choremander", "claim_reward", reward_id=reward["id"], child_id=radha_id)

    assert ha.points(RADHA) == 5, "An unaffordable reward claim still deducted points"


def test_bonus_and_penalty_services_adjust_the_balance(ha: HAClient) -> None:
    """The parent-facing points controls move the balance both directions."""
    krishna_id = ha.child(KRISHNA)["id"]
    before = ha.points(KRISHNA)

    ha.call_service("choremander", "add_points", child_id=krishna_id, points=7, reason="great week")
    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(KRISHNA) == before + 7,
        description="the bonus to land",
    )

    ha.call_service(
        "choremander", "remove_points", child_id=krishna_id, points=3, reason="broke a rule"
    )
    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(KRISHNA) == before + 4,
        description="the penalty to land",
    )


def test_pressing_a_chore_button_entity_awards_points(ha: HAClient) -> None:
    """The button entities are the surface kids actually tap, so drive one directly."""
    entity_id = "button.radha_complete_water_the_plants"
    before = ha.points(RADHA)

    ha.call_service("button", "press", entity_id=entity_id)

    ha.wait_for(
        ha.OVERVIEW,
        lambda _: ha.points(RADHA) == before + 2,
        description=f"pressing {entity_id} to award 2 points",
    )
