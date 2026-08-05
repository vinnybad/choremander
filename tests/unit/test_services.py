"""Service registration, schema validation and handler dispatch.

These tests exercise `custom_components.choremander.__init__`'s service layer
through `hass.services.async_call`, so the voluptuous schemas run for real.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
import pytest
import voluptuous as vol
import yaml

from custom_components.choremander import _async_register_services
from custom_components.choremander.const import DOMAIN
from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import Child, Chore

SERVICES_YAML = Path(__file__).parents[2] / "custom_components" / "choremander" / "services.yaml"

# Every service `_async_register_services` registers, with a schema-valid payload.
SERVICE_PAYLOADS: dict[str, dict[str, Any]] = {
    "complete_chore": {"chore_id": "chore_x", "child_id": "child_x"},
    "approve_chore": {"completion_id": "completion_x"},
    "reject_chore": {"completion_id": "completion_x"},
    "claim_reward": {"reward_id": "reward_x", "child_id": "child_x"},
    "approve_reward": {"claim_id": "claim_x"},
    "add_points": {"child_id": "child_x", "points": 1},
    "remove_points": {"child_id": "child_x", "points": 1},
    "set_chore_order": {"child_id": "child_x", "chore_order": ["chore_x"]},
}
ALL_SERVICES = list(SERVICE_PAYLOADS)


@pytest.fixture
async def household(
    hass: HomeAssistant,
    bypass_frontend_registration: None,
    coordinator: ChoremanderCoordinator,
) -> dict[str, Any]:
    """One child and one no-approval chore, created through the coordinator."""
    child: Child = await coordinator.async_add_child("Krishna")
    chore: Chore = await coordinator.async_add_chore(
        "Make Bed", points=2, requires_approval=False, daily_limit=5
    )
    return {"coordinator": coordinator, "child": child, "chore": chore}


# ---------------------------------------------------------------------------
# Registration surface
# ---------------------------------------------------------------------------


async def test_setup_registers_all_eight_services(
    hass: HomeAssistant, bypass_frontend_registration: None, init_integration: Any
) -> None:
    """Setting up a single entry exposes the full Choremander service API."""
    missing = [s for s in ALL_SERVICES if not hass.services.has_service(DOMAIN, s)]
    assert missing == [], f"these services were never registered: {missing!r}"

    registered = set(hass.services.async_services_for_domain(DOMAIN))
    assert registered == set(ALL_SERVICES), (
        "the registered service set drifted from the expected 8; "
        f"unexpected extras: {sorted(registered - set(ALL_SERVICES))!r}"
    )


def test_services_yaml_documents_seven_of_the_eight_registered_services() -> None:
    """`set_chore_order` is callable but invisible in the Developer Tools UI."""
    documented = set(yaml.safe_load(SERVICES_YAML.read_text()))

    # CURRENT BEHAVIOUR (defect #8, see test plan 6.2) - the reorder card's
    # service is registered in code but missing from services.yaml, so it has
    # no name, description or field hints anywhere in the UI.
    assert documented ^ set(ALL_SERVICES) == {"set_chore_order"}, (
        "services.yaml and the registered services should differ by exactly "
        f"set_chore_order; actual difference: {documented ^ set(ALL_SERVICES)!r}"
    )
    assert len(documented) == 7, f"expected 7 documented services, got {len(documented)}"


async def test_reset_daily_and_preview_sound_constants_are_never_registered(
    hass: HomeAssistant, bypass_frontend_registration: None, init_integration: Any
) -> None:
    """`SERVICE_RESET_DAILY` / `SERVICE_PREVIEW_SOUND` are dead constants."""
    assert not hass.services.has_service(DOMAIN, "reset_daily"), (
        "reset_daily is declared in const.py but has no handler"
    )
    assert not hass.services.has_service(DOMAIN, "preview_sound"), (
        "preview_sound is declared in const.py but has no handler"
    )


# ---------------------------------------------------------------------------
# Missing coordinator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service", ALL_SERVICES)
async def test_handler_logs_and_returns_when_no_coordinator_is_loaded(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
    service: str,
) -> None:
    """Every handler degrades to an ERROR log rather than raising."""
    await _async_register_services(hass)
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger="custom_components.choremander"):
        await hass.services.async_call(DOMAIN, service, SERVICE_PAYLOADS[service], blocking=True)

    matching = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR
        and "No Choremander coordinator available" in record.getMessage()
    ]
    assert len(matching) == 1, (
        f"choremander.{service} should log exactly one 'No Choremander "
        f"coordinator available' error when no entry is loaded, got "
        f"{[r.getMessage() for r in caplog.records]!r}"
    )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


async def test_complete_chore_without_child_id_is_rejected_by_the_schema(
    hass: HomeAssistant, bypass_frontend_registration: None, init_integration: Any
) -> None:
    """A missing required field never reaches the coordinator."""
    with pytest.raises(vol.MultipleInvalid) as excinfo:
        await hass.services.async_call(
            DOMAIN, "complete_chore", {"chore_id": "chore_x"}, blocking=True
        )

    assert "child_id" in str(excinfo.value), (
        f"the validation error should name the missing field, got {excinfo.value}"
    )


async def test_add_points_accepts_zero_despite_the_yaml_minimum_of_one(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """`cv.positive_int` allows 0; the yaml `min: 1` is only a UI hint."""
    coordinator: ChoremanderCoordinator = household["coordinator"]
    child: Child = household["child"]

    await hass.services.async_call(
        DOMAIN, "add_points", {"child_id": child.id, "points": 0}, blocking=True
    )

    after = coordinator.get_child(child.id)
    assert after.points == 0, "adding zero points must not change the balance"
    assert after.total_chores_completed == 0, (
        "a manual adjustment is not a chore, so the lifetime count stays put"
    )
    assert coordinator.storage.get_points_transactions() == [], (
        "nothing moved, so there is nothing to account for in the ledger"
    )


async def test_add_points_rejects_a_negative_amount(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """Negative bonuses are a schema error, not a silent deduction."""
    child: Child = household["child"]
    with pytest.raises(vol.MultipleInvalid):
        await hass.services.async_call(
            DOMAIN, "add_points", {"child_id": child.id, "points": -1}, blocking=True
        )


async def test_add_points_coerces_a_string_amount_to_an_int(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """A websocket client sending "5" still credits 5 points."""
    coordinator: ChoremanderCoordinator = household["coordinator"]
    child: Child = household["child"]

    await hass.services.async_call(
        DOMAIN, "add_points", {"child_id": child.id, "points": "5"}, blocking=True
    )

    assert coordinator.get_child(child.id).points == 5, (
        "cv.positive_int should have coerced the string '5' to 5"
    )


async def test_set_chore_order_wraps_a_bare_string_into_a_list(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """`cv.ensure_list` means a single id need not be wrapped by the caller."""
    coordinator: ChoremanderCoordinator = household["coordinator"]
    child: Child = household["child"]
    chore: Chore = household["chore"]

    await hass.services.async_call(
        DOMAIN,
        "set_chore_order",
        {"child_id": child.id, "chore_order": chore.id},
        blocking=True,
    )

    assert coordinator.get_child(child.id).chore_order == [chore.id], (
        "a bare chore id string should have been stored as a one-element list"
    )


def test_cv_string_coerces_ints_but_rejects_none() -> None:
    """Pins the id-field coercion the service schemas rely on."""
    assert cv.string(1) == "1", "cv.string should stringify an int id"
    with pytest.raises(vol.Invalid):
        cv.string(None)


async def test_numeric_ids_are_coerced_to_strings_before_lookup(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """An int `chore_id` passes validation and fails as the *string* "1"."""
    child: Child = household["child"]
    with pytest.raises(ValueError) as excinfo:
        await hass.services.async_call(
            DOMAIN,
            "complete_chore",
            {"chore_id": 1, "child_id": child.id},
            blocking=True,
        )

    assert str(excinfo.value) == "Chore 1 not found", (
        f"expected the stringified id in the lookup failure, got {excinfo.value}"
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_coordinator_valueerror_propagates_out_of_a_blocking_service_call(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """Domain errors are not swallowed by the service layer."""
    with pytest.raises(ValueError) as excinfo:
        await hass.services.async_call(
            DOMAIN, "add_points", {"child_id": "nope", "points": 5}, blocking=True
        )

    assert str(excinfo.value) == "Child nope not found", (
        f"expected the coordinator's own message, got {excinfo.value}"
    )


async def test_complete_chore_handler_passes_chore_id_before_child_id(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """The handler's positional order matches `async_complete_chore(chore, child)`."""
    coordinator: ChoremanderCoordinator = household["coordinator"]
    spy = AsyncMock(return_value=None)

    with patch.object(coordinator, "async_complete_chore", spy):
        await hass.services.async_call(
            DOMAIN,
            "complete_chore",
            {"chore_id": "chore_x", "child_id": "child_x"},
            blocking=True,
        )

    assert spy.await_args.args == ("chore_x", "child_x"), (
        "complete_chore must forward (chore_id, child_id) positionally; got "
        f"{spy.await_args.args!r}"
    )


async def test_complete_chore_service_awards_points_end_to_end(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """The happy path really moves points through the real coordinator."""
    coordinator: ChoremanderCoordinator = household["coordinator"]
    child: Child = household["child"]
    chore: Chore = household["chore"]

    await hass.services.async_call(
        DOMAIN,
        "complete_chore",
        {"chore_id": chore.id, "child_id": child.id},
        blocking=True,
    )

    assert coordinator.get_child(child.id).points == 2, (
        "completing a 2-point no-approval chore should credit 2 points"
    )


@pytest.mark.parametrize(
    ("service", "payload", "method", "expected_args"),
    [
        ("approve_chore", {"completion_id": "cmp_x"}, "async_approve_chore", ("cmp_x",)),
        ("reject_chore", {"completion_id": "cmp_x"}, "async_reject_chore", ("cmp_x",)),
        (
            "claim_reward",
            {"reward_id": "reward_x", "child_id": "child_x"},
            "async_claim_reward",
            ("reward_x", "child_x"),
        ),
        ("approve_reward", {"claim_id": "claim_x"}, "async_approve_reward", ("claim_x",)),
    ],
)
async def test_approval_services_forward_their_ids_positionally(
    hass: HomeAssistant,
    household: dict[str, Any],
    service: str,
    payload: dict[str, Any],
    method: str,
    expected_args: tuple[str, ...],
) -> None:
    """The four approval/claim handlers unpack `call.data` in a fixed order.

    `claim_reward` in particular must forward `(reward_id, child_id)` - the
    reverse order would silently charge the wrong child.
    """
    coordinator: ChoremanderCoordinator = household["coordinator"]
    spy = AsyncMock(return_value=None)

    with patch.object(coordinator, method, spy):
        await hass.services.async_call(DOMAIN, service, payload, blocking=True)

    assert spy.await_args.args == expected_args, (
        f"{service} must forward {expected_args!r} positionally to {method}; got "
        f"{spy.await_args.args!r}"
    )
    assert spy.await_args.kwargs == {}, f"{service} passes nothing by keyword"


async def test_the_chore_approval_services_run_end_to_end_through_the_coordinator(
    hass: HomeAssistant, coordinator: ChoremanderCoordinator
) -> None:
    """`complete_chore` -> `approve_chore` credits; a reject leaves the balance."""
    child: Child = await coordinator.async_add_child("Krishna")
    chore: Chore = await coordinator.async_add_chore(
        "Homework", points=5, requires_approval=True, daily_limit=5
    )

    for _ in range(2):
        await hass.services.async_call(
            DOMAIN,
            "complete_chore",
            {"chore_id": chore.id, "child_id": child.id},
            blocking=True,
        )
    pending = coordinator.storage.get_pending_completions()
    assert len(pending) == 2, "an approval-required chore parks the completion"
    assert coordinator.get_child(child.id).points == 0, "nothing is credited yet"

    await hass.services.async_call(
        DOMAIN, "approve_chore", {"completion_id": pending[0].id}, blocking=True
    )
    assert coordinator.get_child(child.id).points == 5

    await hass.services.async_call(
        DOMAIN, "reject_chore", {"completion_id": pending[1].id}, blocking=True
    )
    assert coordinator.get_child(child.id).points == 5, (
        "a rejection must neither credit nor claw back points"
    )
    assert coordinator.storage.get_pending_completions() == [], (
        "both completions have now been resolved"
    )


async def test_the_reward_services_deduct_on_claim_and_clear_on_approval(
    hass: HomeAssistant, coordinator: ChoremanderCoordinator
) -> None:
    """`claim_reward` debits immediately; `approve_reward` only clears the claim."""
    child: Child = await coordinator.async_add_child("Krishna")
    await coordinator.async_add_points(child.id, 20)
    reward = await coordinator.async_add_reward("Ice Cream", cost=8, override_point_value=True)

    await hass.services.async_call(
        DOMAIN,
        "claim_reward",
        {"reward_id": reward.id, "child_id": child.id},
        blocking=True,
    )
    assert coordinator.get_child(child.id).points == 12, "the cost is debited on claim"
    claims = coordinator.storage.get_pending_reward_claims()
    assert len(claims) == 1

    await hass.services.async_call(
        DOMAIN, "approve_reward", {"claim_id": claims[0].id}, blocking=True
    )
    assert coordinator.storage.get_pending_reward_claims() == []
    assert coordinator.get_child(child.id).points == 12, "approving must not debit a second time"


async def test_remove_points_handler_forwards_child_points_and_reason(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """The optional `reason` defaults to "" and is passed through positionally."""
    coordinator: ChoremanderCoordinator = household["coordinator"]
    spy = AsyncMock(return_value=None)

    with patch.object(coordinator, "async_remove_points", spy):
        await hass.services.async_call(
            DOMAIN, "remove_points", {"child_id": "child_x", "points": 3}, blocking=True
        )

    assert spy.await_args.args == ("child_x", 3, ""), (
        f"remove_points should forward (child_id, points, reason); got {spy.await_args.args!r}"
    )


async def test_reason_travels_from_the_service_call_into_the_ledger(
    hass: HomeAssistant, household: dict[str, Any]
) -> None:
    """`reason` reaches the coordinator and is recorded against the adjustment."""
    coordinator: ChoremanderCoordinator = household["coordinator"]
    child: Child = household["child"]

    await hass.services.async_call(
        DOMAIN,
        "add_points",
        {"child_id": child.id, "points": 4, "reason": "Helped a sibling"},
        blocking=True,
    )

    assert coordinator.get_child(child.id).points == 4

    ledger = coordinator.storage.get_points_transactions()
    assert len(ledger) == 1, "one adjustment, one ledger entry"
    entry = ledger[0]
    assert entry.event_type == "points_added"
    assert entry.points == 4
    assert entry.reason == "Helped a sibling"
    assert entry.child_id == child.id
