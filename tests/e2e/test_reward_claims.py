"""End-to-end coverage for claiming rewards against the live container.

These exist because the rewards card had no claim path at all (issue #3), and
the first fix for it called `claim_reward` without `child_id`. The service
declares `child_id` as required and deducts from exactly that child, so the
rules the card has to honour are asserted here against the real backend rather
than inferred from the card's own code.
"""

from __future__ import annotations

import pytest

from .ha_client import HAClient, HomeAssistantError

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("restore_points")]

# Assigned to Krishna alone with override_point_value set, so its cost is the
# flat 50 rather than a dynamically recalculated figure.
FIXED_PRICE_REWARD = "New Video Game"
FIXED_PRICE = 50

# assigned_to is empty and override_point_value is false, so every child gets
# their own calculated cost. Used to prove the charge is the per-child cost.
DYNAMIC_REWARD = "30 Min Screen Time"


APPROVALS = "sensor.pending_approvals"


def _claims(ha: HAClient) -> list[dict]:
    """Reward claims live on the approvals sensor, not the overview."""
    return ha.attributes(APPROVALS).get("reward_claims", [])


def _grant(ha: HAClient, child_id: str, name: str, amount: int) -> None:
    """Top a child up so a reward becomes affordable.

    Waits for the exact expected total rather than a lower bound, so a test
    cannot read a balance the service call has not landed in yet.
    """
    target = ha.points(name) + amount
    ha.call_service("choremander", "add_points", child_id=child_id, points=amount)
    ha.wait_for(
        HAClient.OVERVIEW,
        lambda s: any(
            c["id"] == child_id and c["points"] == target for c in s["attributes"]["children"]
        ),
        description=f"{name}'s balance to reach {target}",
    )


def test_claiming_deducts_the_cost_from_the_claiming_child(ha: HAClient) -> None:
    """The happy path the card drives: points leave the claimant's balance."""
    krishna = ha.child("Krishna")
    _grant(ha, krishna["id"], "Krishna", FIXED_PRICE)

    before = ha.points("Krishna")
    radha_before = ha.points("Radha")
    reward = ha.reward(FIXED_PRICE_REWARD)

    ha.call_service("choremander", "claim_reward", reward_id=reward["id"], child_id=krishna["id"])
    ha.wait_for(
        HAClient.OVERVIEW,
        lambda s: next(c["points"] for c in s["attributes"]["children"] if c["name"] == "Krishna")
        == before - FIXED_PRICE,
        description=f"Krishna's balance to fall by {FIXED_PRICE}",
    )

    assert ha.points("Radha") == radha_before, (
        "Claiming charged a child other than the claimant. The card picks who "
        "pays, so a mismatch here means the card can spend the wrong balance."
    )


def test_claiming_records_a_claim_for_the_named_child(ha: HAClient) -> None:
    """The claim is attributed to whoever the card named, not to a default."""
    radha = ha.child("Radha")
    _grant(ha, radha["id"], "Radha", 30)

    reward = ha.reward("New Book")
    before = len(_claims(ha))

    ha.call_service("choremander", "claim_reward", reward_id=reward["id"], child_id=radha["id"])
    ha.wait_for(
        APPROVALS,
        lambda s: len(s["attributes"].get("reward_claims", [])) == before + 1,
        description="the claim to be recorded",
    )

    newest = _claims(ha)[-1]
    assert newest["child_id"] == radha["id"]
    assert newest["reward_id"] == reward["id"]


def test_child_id_is_required(ha: HAClient) -> None:
    """Omitting child_id is rejected outright.

    This is the contract the card's first fix broke: it sent child_id only when
    the card was scoped to one child, so an unscoped card - which is how the
    parent dashboard configures it - failed on every claim.
    """
    reward = ha.reward(FIXED_PRICE_REWARD)

    with pytest.raises(HomeAssistantError):
        ha.call_service("choremander", "claim_reward", reward_id=reward["id"])


def test_claiming_without_enough_points_changes_nothing(ha: HAClient) -> None:
    """The backend refuses, and no points move.

    The card disables the button below the cost, but the guard has to hold on
    the server too - the card's own arithmetic is not what protects the balance.
    """
    radha = ha.child("Radha")
    reward = ha.reward(FIXED_PRICE_REWARD)
    before = ha.points("Radha")
    claims_before = len(_claims(ha))

    assert before < FIXED_PRICE, (
        f"Radha starts on {before} points, which already covers the "
        f"{FIXED_PRICE}-point reward. This test cannot prove anything."
    )

    with pytest.raises(HomeAssistantError):
        ha.call_service("choremander", "claim_reward", reward_id=reward["id"], child_id=radha["id"])

    assert ha.points("Radha") == before
    assert len(_claims(ha)) == claims_before


def test_dynamic_rewards_charge_the_per_child_calculated_cost(ha: HAClient) -> None:
    """Dynamic pricing, not `cost`, decides what a child pays.

    Dynamic pricing is the default, and each child gets their own figure. The
    card has to show and deduct that figure - showing `reward.cost` would tell a
    child they spent something other than what left their balance.
    """
    krishna = ha.child("Krishna")
    reward = ha.reward(DYNAMIC_REWARD)
    calculated = reward["calculated_costs"][krishna["id"]]

    assert calculated != reward["cost"], (
        f"{DYNAMIC_REWARD} is priced at its manual cost ({reward['cost']}), so "
        "this test cannot tell the two apart. Check the fixture still has "
        "override_point_value false."
    )

    _grant(ha, krishna["id"], "Krishna", calculated)
    before = ha.points("Krishna")

    ha.call_service("choremander", "claim_reward", reward_id=reward["id"], child_id=krishna["id"])
    ha.wait_for(
        HAClient.OVERVIEW,
        lambda s: next(c["points"] for c in s["attributes"]["children"] if c["name"] == "Krishna")
        == before - calculated,
        description=f"Krishna to be charged the calculated cost of {calculated}",
    )
