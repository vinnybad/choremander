"""Builds the activity feed published by the activity sensor.

Kept separate from sensor.py, and free of Home Assistant imports, so the shape
of the feed can be tested directly.

The feed is one merged, chronologically sorted stream rather than the two lists
a card would otherwise have to interleave itself. Two rules shape it:

* Every entry carries a structured ``event_type``. The card decides wording,
  icon and colour from that; it never parses text to work out what happened.
* Keys are omitted when they have nothing to say. Home Assistant's recorder
  refuses to store a state whose attributes exceed 16KiB, and this feed shares
  that budget with everything else on the entity.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .const import EVENT_CHORE_PENDING
from .models import Child, Chore, ChoreCompletion, PointsTransaction, Reward, format_datetime


def build_activity_feed(
    transactions: list[PointsTransaction],
    pending_completions: list[ChoreCompletion],
    children: list[Child],
    chores: list[Chore],
    rewards: list[Reward],
    limit: int,
) -> list[dict[str, Any]]:
    """Return the most recent activity events, newest first.

    ``transactions`` covers everything that moved a balance. Pending
    completions are folded in because they are activity too, even though no
    points have changed hands yet - and because once approved the pending entry
    is replaced by the ledger entry, so nothing is ever shown twice.
    """
    child_names = {child.id: child.name for child in children}
    chore_names = {chore.id: chore.name for chore in chores}
    reward_names = {reward.id: reward.name for reward in rewards}
    reward_icons = {reward.id: reward.icon for reward in rewards}

    # (sort key, event). The key is the datetime itself rather than the
    # formatted string: a local-timezone string sorts wrongly across a
    # daylight-saving change, when the UTC offset in it shifts.
    events: list[tuple[datetime, dict[str, Any]]] = []

    for transaction in transactions:
        event: dict[str, Any] = {
            "id": transaction.id,
            "event_type": transaction.event_type,
            "child_id": transaction.child_id,
            "child_name": child_names.get(transaction.child_id, ""),
            "points": transaction.points,
            "occurred_at": format_datetime(transaction.created_at),
        }
        # A chore or reward can be deleted while its ledger entry survives. The
        # entry is still true and still worth showing, so fall back to the
        # generic wording the card uses when no name is present.
        if transaction.chore_id and transaction.chore_id in chore_names:
            event["chore_name"] = chore_names[transaction.chore_id]
        if transaction.reward_id and transaction.reward_id in reward_names:
            event["reward_name"] = reward_names[transaction.reward_id]
            event["reward_icon"] = reward_icons[transaction.reward_id]
        if transaction.reason:
            event["reason"] = transaction.reason
        events.append((_sort_key(transaction.created_at), event))

    for completion in pending_completions:
        chore = chore_names.get(completion.chore_id)
        event = {
            "id": completion.id,
            "event_type": EVENT_CHORE_PENDING,
            "child_id": completion.child_id,
            "child_name": child_names.get(completion.child_id, ""),
            # Nothing has been credited yet. The card shows what is at stake
            # rather than a balance change, so this stays zero and the chore's
            # worth travels separately.
            "points": 0,
            "occurred_at": format_datetime(completion.completed_at),
        }
        if chore is not None:
            event["chore_name"] = chore
        pending_points = next(
            (c.points for c in chores if c.id == completion.chore_id), 0
        )
        if pending_points:
            event["pending_points"] = pending_points
        events.append((_sort_key(completion.completed_at), event))

    # Newest first, which is the order the card renders.
    events.sort(key=lambda entry: entry[0], reverse=True)
    return [event for _, event in events[: max(0, limit)]]


def _sort_key(value: datetime) -> datetime:
    """Return an absolute instant, in UTC, safe to compare against any other.

    Two details make the conversion necessary rather than cosmetic:

    * A naive timestamp cannot be ordered against an aware one at all - Python
      raises. Everything written through the models is aware, but a hand-edited
      storage file need not be, and that would take down the whole sensor.
    * Comparing two aware datetimes that share a tzinfo skips the UTC offset
      entirely and compares them as if naive, which ignores ``fold``. During
      the repeated hour of a daylight-saving change that puts 01:15 before
      01:45 even when the 01:15 happened half an hour later. Converting to UTC
      first makes every comparison an absolute-instant one.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
