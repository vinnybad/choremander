"""Tests for the version 1 to version 2 storage migration.

A user's storage file is the one thing that cannot be recreated if this goes
wrong, so the malformed-input cases matter as much as the happy path. The
migration runs against the raw dict, with no Home Assistant involved, which is
what lets these be exhaustive.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

import pytest

from custom_components.choremander.const import MAX_POINTS_TRANSACTIONS
from custom_components.choremander.migration import migrate_v1_to_v2
from custom_components.choremander.models import PointsTransaction


def at(day: int, hour: int = 12) -> str:
    """An ISO timestamp on the given day of August 2026."""
    return datetime(2026, 8, day, hour, tzinfo=UTC).isoformat().replace("+00:00", "Z")


def v1_payload(**overrides: Any) -> dict[str, Any]:
    """A representative version 1 storage blob."""
    data: dict[str, Any] = {
        "children": [{"id": "c1", "name": "Krishna"}, {"id": "c2", "name": "Radha"}],
        "chores": [
            {"id": "bed", "name": "Make Bed", "points": 10},
            {"id": "dishes", "name": "Dishes", "points": 5},
        ],
        "rewards": [{"id": "screen", "name": "Screen Time", "cost": 40}],
        "completions": [],
        "reward_claims": [],
        "points_name": "Stars",
        "points_icon": "mdi:star",
    }
    data.update(overrides)
    return data


def ledger(data: dict[str, Any]) -> list[PointsTransaction]:
    """The migrated ledger, parsed back into models."""
    return [PointsTransaction.from_dict(t) for t in data["points_transactions"]]


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_an_empty_v1_file_gains_an_empty_ledger() -> None:
    migrated = migrate_v1_to_v2(v1_payload())
    assert migrated["points_transactions"] == []


def test_every_other_key_survives_untouched() -> None:
    original = v1_payload()
    migrated = migrate_v1_to_v2(v1_payload())

    for key, value in original.items():
        assert migrated[key] == value, f"{key} must not be disturbed by the migration"


def test_an_approved_completion_becomes_a_credit() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1, 8),
                    "approved": True,
                    "approved_at": at(1, 9),
                    "points_awarded": 10,
                }
            ]
        )
    )

    entry = ledger(migrated)[0]
    assert entry.event_type == "chore_approved"
    assert entry.points == 10
    assert entry.child_id == "c1"
    assert entry.chore_id == "bed"
    assert entry.related_id == "comp1", "the entry points back at the completion"
    assert entry.created_at == datetime(2026, 8, 1, 9, tzinfo=UTC), (
        "an approved completion is dated when it was approved, not when it was done"
    )


def test_a_reward_claim_becomes_a_debit() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            reward_claims=[
                {
                    "id": "claim1",
                    "reward_id": "screen",
                    "child_id": "c2",
                    "claimed_at": at(2),
                    "approved": True,
                }
            ]
        )
    )

    entry = ledger(migrated)[0]
    assert entry.event_type == "reward_claimed"
    assert entry.points == -40, "a claim is a debit, so the sign is negative"
    assert entry.reward_id == "screen"
    assert entry.related_id == "claim1"


def test_entries_come_out_oldest_first() -> None:
    """Matching the order the ledger is appended to at runtime."""
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "late",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(9),
                    "approved": True,
                    "points_awarded": 10,
                },
                {
                    "id": "early",
                    "chore_id": "dishes",
                    "child_id": "c1",
                    "completed_at": at(3),
                    "approved": True,
                    "points_awarded": 5,
                },
            ],
            reward_claims=[
                {
                    "id": "middle",
                    "reward_id": "screen",
                    "child_id": "c1",
                    "claimed_at": at(6),
                }
            ],
        )
    )

    assert [e.related_id for e in ledger(migrated)] == ["early", "middle", "late"]


def test_a_completion_awaiting_approval_is_not_backfilled() -> None:
    """Nothing has been credited yet, so there is nothing to account for."""
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": False,
                    "points_awarded": 0,
                }
            ]
        )
    )
    assert migrated["points_transactions"] == []


def test_points_awarded_wins_over_the_chores_current_worth() -> None:
    """A chore repriced since the completion must not rewrite history."""
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 3,
                }
            ]
        )
    )
    assert ledger(migrated)[0].points == 3, "not the 10 the chore is worth today"


def test_a_completion_predating_points_awarded_falls_back_to_the_chore() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                }
            ]
        )
    )
    assert ledger(migrated)[0].points == 10


def test_a_completion_dated_only_by_completed_at_still_migrates() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(4, 7),
                    "approved": True,
                    "points_awarded": 10,
                }
            ]
        )
    )
    assert ledger(migrated)[0].created_at == datetime(2026, 8, 4, 7, tzinfo=UTC)


def test_the_ledger_is_trimmed_to_the_same_ceiling_used_at_runtime() -> None:
    """A busy family must not arrive at version 2 already over the cap."""
    completions = [
        {
            "id": f"comp{i}",
            "chore_id": "bed",
            "child_id": "c1",
            "completed_at": datetime(2020, 1, 1, tzinfo=UTC).isoformat(),
            "approved": True,
            "points_awarded": 1,
        }
        for i in range(MAX_POINTS_TRANSACTIONS + 250)
    ]
    # Distinct timestamps so "newest kept" is meaningful.
    for index, completion in enumerate(completions):
        completion["completed_at"] = (
            datetime(2020, 1, 1, tzinfo=UTC).replace(year=2020 + index // 300).isoformat()
        )

    migrated = migrate_v1_to_v2(v1_payload(completions=completions))
    assert len(migrated["points_transactions"]) == MAX_POINTS_TRANSACTIONS


def test_the_newest_entries_are_the_ones_kept() -> None:
    completions = [
        {
            "id": f"comp{i}",
            "chore_id": "bed",
            "child_id": "c1",
            "completed_at": datetime(2026, 1, 1, tzinfo=UTC)
            .replace(minute=i % 60, hour=(i // 60) % 24, day=1 + i // 1440)
            .isoformat(),
            "approved": True,
            "points_awarded": 1,
        }
        for i in range(MAX_POINTS_TRANSACTIONS + 10)
    ]

    migrated = migrate_v1_to_v2(v1_payload(completions=completions))
    kept = {e.related_id for e in ledger(migrated)}

    assert "comp0" not in kept, "the oldest entries fall off the front"
    assert f"comp{MAX_POINTS_TRANSACTIONS + 9}" in kept, "the newest is retained"


# ---------------------------------------------------------------------------
# Idempotency and re-entry
# ---------------------------------------------------------------------------


def test_running_the_migration_twice_changes_nothing() -> None:
    once = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 10,
                }
            ]
        )
    )
    twice = migrate_v1_to_v2(dict(once))

    assert twice["points_transactions"] == once["points_transactions"], (
        "an existing ledger must never be regenerated - that would duplicate "
        "every entry and, worse, discard entries the backfill cannot recreate"
    )


def test_an_existing_ledger_is_left_alone_even_if_it_is_empty() -> None:
    """An empty list is a real answer, not a missing one."""
    data = v1_payload(
        points_transactions=[],
        completions=[
            {
                "id": "comp1",
                "chore_id": "bed",
                "child_id": "c1",
                "completed_at": at(1),
                "approved": True,
                "points_awarded": 10,
            }
        ],
    )
    assert migrate_v1_to_v2(data)["points_transactions"] == []


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("junk", [None, [], "corrupt", 42])
def test_a_payload_that_is_not_a_dict_yields_a_usable_shape(junk: Any) -> None:
    """Startup must not be taken down by an unreadable file."""
    assert migrate_v1_to_v2(junk) == {"points_transactions": []}


@pytest.mark.parametrize("key", ["completions", "reward_claims", "chores", "rewards"])
def test_a_missing_collection_is_treated_as_empty(key: str) -> None:
    data = v1_payload()
    del data[key]
    assert migrate_v1_to_v2(data)["points_transactions"] == []


@pytest.mark.parametrize("junk", [None, "not a list", {}, 7])
def test_a_collection_of_the_wrong_type_is_treated_as_empty(junk: Any) -> None:
    assert migrate_v1_to_v2(v1_payload(completions=junk))["points_transactions"] == []


def test_non_dict_entries_inside_a_collection_are_skipped() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                "garbage",
                None,
                {
                    "id": "good",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 10,
                },
            ]
        )
    )
    assert [e.related_id for e in ledger(migrated)] == ["good"]


def test_a_completion_with_an_unparseable_date_is_skipped_not_fatal() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "bad",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": None,
                    "approved": True,
                    "points_awarded": 10,
                },
                {
                    "id": "good",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 10,
                },
            ]
        )
    )
    assert [e.related_id for e in ledger(migrated)] == ["good"]


def test_a_claim_for_a_deleted_reward_is_skipped() -> None:
    """The cost is unknowable, and inventing one would misstate the history."""
    migrated = migrate_v1_to_v2(
        v1_payload(
            reward_claims=[
                {"id": "claim1", "reward_id": "gone", "child_id": "c1", "claimed_at": at(1)}
            ]
        )
    )
    assert migrated["points_transactions"] == []


def test_a_completion_for_a_deleted_chore_still_migrates_when_points_are_known() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "gone",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 6,
                }
            ]
        )
    )
    entry = ledger(migrated)[0]
    assert entry.points == 6, "the award happened, whatever became of the chore"
    assert entry.chore_id == "gone"


def test_a_zero_point_completion_creates_no_entry() -> None:
    """Nothing moved, so there is nothing to explain."""
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "comp1",
                    "chore_id": "gone",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 0,
                }
            ]
        )
    )
    assert migrated["points_transactions"] == []


def test_every_backfilled_entry_has_a_unique_id() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": f"comp{i}",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1 + i),
                    "approved": True,
                    "points_awarded": 10,
                }
                for i in range(20)
            ]
        )
    )
    ids = [e.id for e in ledger(migrated)]
    assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# Hostile input
# ---------------------------------------------------------------------------
#
# A migration runs inside `async_setup_entry`. An exception here does not
# degrade a feature - it stops the integration loading, and because Home
# Assistant only writes the upgraded file once the migration returns, the file
# stays at the old version and the failure repeats on every restart. There is no
# recovery short of hand-editing `.storage`. Every one of these cases raised
# before the coercion helpers were added.


@pytest.mark.parametrize(
    "bad_date",
    ["", "not-a-date", "2026-13-45", "  ", "2026/08/01", 12345, [], {}],
)
def test_an_unreadable_completion_timestamp_is_skipped_not_fatal(bad_date: Any) -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "bad",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": bad_date,
                    "approved": True,
                    "points_awarded": 10,
                },
                {
                    "id": "good",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 10,
                },
            ]
        )
    )
    assert [e.related_id for e in ledger(migrated)] == ["good"], (
        "one unreadable record must cost that record, never the integration"
    )


@pytest.mark.parametrize("bad_points", ["abc", "", None, [], {}, "12abc"])
def test_non_numeric_points_are_skipped_not_fatal(bad_points: Any) -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            completions=[
                {
                    "id": "bad",
                    "chore_id": "gone",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": bad_points,
                }
            ]
        )
    )
    assert migrated["points_transactions"] == []


def test_a_non_numeric_reward_cost_is_skipped_not_fatal() -> None:
    migrated = migrate_v1_to_v2(
        v1_payload(
            rewards=[{"id": "screen", "name": "Screen Time", "cost": "lots"}],
            reward_claims=[
                {"id": "c", "reward_id": "screen", "child_id": "c1", "claimed_at": at(1)}
            ],
        )
    )
    assert migrated["points_transactions"] == []


@pytest.mark.parametrize("bad_id", [["a", "b"], {"x": 1}, None, 7])
def test_an_unhashable_record_id_is_skipped_not_fatal(bad_id: Any) -> None:
    """A list id used as a dict key raises TypeError, not ValueError."""
    migrated = migrate_v1_to_v2(
        v1_payload(
            chores=[{"id": bad_id, "name": "Broken", "points": 3}],
            completions=[
                {
                    "id": "x",
                    "chore_id": bad_id,
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                }
            ],
        )
    )
    assert migrated["points_transactions"] == []


def test_the_migrated_payload_is_always_json_serialisable() -> None:
    """Home Assistant writes the return value straight back to disk."""
    migrated = migrate_v1_to_v2(
        v1_payload(
            children=[{"id": "c1", "name": "Krishna"}, "junk", None],
            completions=[
                "junk",
                {
                    "id": "ok",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 10,
                },
            ],
        )
    )
    json.dumps(migrated)


# ---------------------------------------------------------------------------
# Repairing zeroed lifetime counters
# ---------------------------------------------------------------------------


def _with_history(**child_overrides: Any) -> dict[str, Any]:
    child = {"id": "c1", "name": "Krishna"}
    child.update(child_overrides)
    return v1_payload(
        children=[child],
        completions=[
            {
                "id": f"comp{i}",
                "chore_id": "bed",
                "child_id": "c1",
                "completed_at": at(1 + i),
                "approved": True,
                "points_awarded": 10,
            }
            for i in range(4)
        ],
    )


def test_zeroed_lifetime_counters_are_rebuilt_from_the_history() -> None:
    """Otherwise the streak card shows "First!" locked above proof of a week's work.

    A normally written v1 file already carries these counters. One that was
    hand-edited or restored from a partial backup can carry the completions
    without them, and the milestones then contradict the dots beside them.
    """
    migrated = migrate_v1_to_v2(_with_history())
    child = migrated["children"][0]

    assert child["total_chores_completed"] == 4
    assert child["total_points_earned"] == 40


def test_real_counters_are_never_overwritten() -> None:
    """History can be pruned; a stored lifetime total cannot be second-guessed."""
    migrated = migrate_v1_to_v2(_with_history(total_chores_completed=900, total_points_earned=9000))
    child = migrated["children"][0]

    assert child["total_chores_completed"] == 900
    assert child["total_points_earned"] == 9000


def test_a_child_with_no_history_is_left_alone() -> None:
    migrated = migrate_v1_to_v2(v1_payload())
    assert "total_chores_completed" not in migrated["children"][0]


def test_only_approved_work_counts_toward_the_rebuilt_totals() -> None:
    data = _with_history()
    data["completions"].append(
        {
            "id": "pending",
            "chore_id": "bed",
            "child_id": "c1",
            "completed_at": at(9),
            "approved": False,
        }
    )
    child = migrate_v1_to_v2(data)["children"][0]
    assert child["total_chores_completed"] == 4, "the pending one is not counted"


def test_an_unhashable_child_id_is_skipped_not_fatal() -> None:
    """The counter repair reads children too, and must guard its keys.

    Every id used as a dict key goes through the same guard. This one was
    missed when the repair was added, and a list id raised `TypeError` from
    `dict.get` - taking down startup exactly as an unparseable timestamp did.
    """
    migrated = migrate_v1_to_v2(
        v1_payload(
            children=[{"id": ["unhashable"], "name": "Krishna"}, {"id": "c1", "name": "Radha"}],
            completions=[
                {
                    "id": "comp",
                    "chore_id": "bed",
                    "child_id": "c1",
                    "completed_at": at(1),
                    "approved": True,
                    "points_awarded": 10,
                }
            ],
        )
    )

    json.dumps(migrated)
    assert migrated["children"][1]["total_chores_completed"] == 1, (
        "the readable child is still repaired"
    )
