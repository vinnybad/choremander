"""Storage migrations for Choremander.

These are plain functions over the raw storage dict, with no Home Assistant
imports, so they can be unit tested directly against realistic payloads.
``ChoremanderStore._async_migrate_func`` in storage.py is the only caller.

Each migration must be safe to run against data that is empty, partially
populated, or malformed - a user's storage file is the one thing we cannot
recreate if we get this wrong.
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from .const import (
    EVENT_CHORE_APPROVED,
    EVENT_REWARD_CLAIMED,
    MAX_POINTS_TRANSACTIONS,
)
from .models import PointsTransaction, generate_id, parse_datetime

_LOGGER = logging.getLogger(__name__)


def migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Add the points ledger and backfill it from existing records.

    Version 1 had no ledger, so an upgrading family would otherwise open the
    new activity card to an empty screen and reasonably assume it was broken.
    Chore completions and reward claims both carry a timestamp, so the bulk of
    the history can be reconstructed.

    Two things cannot be recovered, and are simply absent from the backfilled
    ledger rather than guessed at:

    * Manual bonuses and penalties. Version 1 accepted a ``reason`` on
      ``add_points``/``remove_points`` and discarded it along with the rest of
      the adjustment, so there is no record that they happened.
    * The exact cost charged for a historical reward claim. Costs can be
      calculated dynamically from the chores of the day, so a claim made months
      ago may have cost something other than what the same reward costs now.
      The reward's current cost is used as the closest available figure.

    Both are noted in the changelog rather than papered over.
    """
    if not isinstance(data, dict):
        # Nothing sensible to migrate. Hand back a fresh, valid shape rather
        # than letting a malformed file take the integration down on startup.
        _LOGGER.warning("Storage payload was not a dict; starting from empty data")
        return {"points_transactions": []}

    if isinstance(data.get("points_transactions"), list):
        # Already present - a re-run, or data written by a newer version that
        # was then rolled back. Leave it alone.
        return data

    chore_points = _points_by_id(data.get("chores"), "points")
    reward_costs = _points_by_id(data.get("rewards"), "cost")

    transactions: list[PointsTransaction] = []

    for completion in _as_list(data.get("completions")):
        if not isinstance(completion, dict) or not completion.get("approved"):
            continue
        # points_awarded is authoritative when set; older records that predate
        # it fall back to what the chore is worth now.
        points = _as_int(completion.get("points_awarded")) or chore_points.get(
            _as_key(completion.get("chore_id")), 0
        )
        if not points:
            continue
        occurred_at = _parse(
            completion.get("approved_at") or completion.get("completed_at")
        )
        if occurred_at is None:
            continue
        transactions.append(
            PointsTransaction(
                child_id=_as_str(completion.get("child_id")),
                points=points,
                event_type=EVENT_CHORE_APPROVED,
                created_at=occurred_at,
                chore_id=_as_str(completion.get("chore_id")),
                related_id=_as_str(completion.get("id")),
                id=generate_id(),
            )
        )

    for claim in _as_list(data.get("reward_claims")):
        if not isinstance(claim, dict):
            continue
        cost = reward_costs.get(_as_key(claim.get("reward_id")), 0)
        if not cost:
            continue
        occurred_at = _parse(claim.get("claimed_at"))
        if occurred_at is None:
            continue
        transactions.append(
            PointsTransaction(
                child_id=_as_str(claim.get("child_id")),
                points=-cost,
                event_type=EVENT_REWARD_CLAIMED,
                created_at=occurred_at,
                reward_id=_as_str(claim.get("reward_id")),
                related_id=_as_str(claim.get("id")),
                id=generate_id(),
            )
        )

    # Oldest first, matching the append order the ledger uses at runtime, then
    # trimmed to the same ceiling so a busy family does not arrive at version 2
    # already over the cap.
    transactions.sort(key=lambda entry: entry.created_at)
    trimmed = transactions[-MAX_POINTS_TRANSACTIONS:]

    data["points_transactions"] = [entry.to_dict() for entry in trimmed]

    _repair_empty_lifetime_counters(data)

    _LOGGER.info(
        "Migrated Choremander storage to version 2: backfilled %d ledger "
        "entries from %d completions and %d reward claims",
        len(trimmed),
        len(_as_list(data.get("completions"))),
        len(_as_list(data.get("reward_claims"))),
    )
    return data


def _repair_empty_lifetime_counters(data: dict[str, Any]) -> None:
    """Rebuild a child's lifetime totals when they are zero but work exists.

    ``total_chores_completed`` and ``total_points_earned`` are running counters,
    incremented as chores are approved. A version 1 file written by a normal
    install therefore already has them, and this leaves those alone.

    A file that has been hand-edited, or restored from a partial backup, can
    carry a full completion history against zeroed counters - and the streak
    card reads those counters for its milestones. Left unrepaired the card shows
    "First!" as *locked* directly above a strip of dots proving a fortnight of
    work, which reads as broken rather than as missing data.

    Only counters that are zero are touched, so a real total is never
    overwritten by one reconstructed from history that may since have been
    pruned.
    """
    completions = _as_list(data.get("completions"))
    if not completions:
        return

    chore_points = _points_by_id(data.get("chores"), "points")

    counted: dict[str, list[int]] = {}
    for completion in completions:
        if not isinstance(completion, dict) or not completion.get("approved"):
            continue
        points = _as_int(completion.get("points_awarded")) or chore_points.get(
            _as_key(completion.get("chore_id")), 0
        )
        counted.setdefault(_as_str(completion.get("child_id")), []).append(points)

    for child in _as_list(data.get("children")):
        if not isinstance(child, dict):
            continue
        # _as_key, not the raw value: a list or dict id raises TypeError from
        # dict.get, which would take down startup exactly as an unparseable
        # timestamp used to.
        awards = counted.get(_as_key(child.get("id")))
        if not awards:
            continue
        if not child.get("total_chores_completed"):
            child["total_chores_completed"] = len(awards)
        if not child.get("total_points_earned"):
            child["total_points_earned"] = sum(awards)


def _as_list(value: Any) -> list[Any]:
    """Return ``value`` if it is a list, otherwise an empty list."""
    return value if isinstance(value, list) else []


# Everything below coerces rather than trusts. A migration runs during
# `async_setup_entry`, so an exception here does not degrade a feature - it
# stops the integration loading at all, and because Home Assistant only writes
# the upgraded file once the migration returns, the file stays at the old
# version and the failure repeats on every restart. A storage file that has been
# hand-edited, merged badly, or restored from a half-written backup must
# therefore lose the odd unreadable record, never the whole integration.


def _as_int(value: Any) -> int:
    """Coerce to an int, treating anything unusable as zero."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_str(value: Any) -> str:
    """Coerce to a string, treating anything unusable as empty."""
    return value if isinstance(value, str) else ""


def _as_key(value: Any) -> str | None:
    """Return a value safe to use as a dict key, or None if it is not."""
    return value if isinstance(value, str) else None


def _parse(value: Any) -> datetime | None:
    """Parse a stored timestamp, returning None for anything unreadable."""
    try:
        return parse_datetime(value)
    except (TypeError, ValueError):
        return None


def _points_by_id(rows: Any, field: str) -> dict[str, int]:
    """Map id -> integer value for a collection of stored records."""
    mapping: dict[str, int] = {}
    for row in _as_list(rows):
        if not isinstance(row, dict):
            continue
        key = _as_key(row.get("id"))
        if key is None:
            continue
        mapping[key] = _as_int(row.get(field))
    return mapping
