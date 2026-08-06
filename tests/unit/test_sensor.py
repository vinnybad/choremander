"""Tests for the Choremander sensor platform.

The Lovelace cards in `www/` read these entities purely by attribute *key*.
Renaming a key, or changing the shape of an entry inside `children`, `chores`,
`rewards`, `todays_completions` or `chore_completions`, breaks the cards
silently - the card simply renders nothing. Every payload assertion below is
therefore an exact key-set comparison, not a subset check.

Numbers come from the worked pricing examples in the test plan (section 4),
derived from the dev seed reproduced in `SEED` below.
"""

from __future__ import annotations

import copy
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander import sensor as sensor_platform
from custom_components.choremander.const import DOMAIN
from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.sensor import (
    ChildPointsSensor,
    ChildStatsSensor,
    ChoremandorOverallStatsSensor,
    PendingApprovalsSensor,
)
from tests.unit.dev_seed import DEV_SEED as _COMMITTED_SEED

ENTRY_ID = "seed_entry"
STORE_KEY = f"choremander.storage.{ENTRY_ID}"

OVERVIEW = "sensor.choremander_overview"
PENDING_APPROVALS = "sensor.pending_approvals"

# The committed dev seed, from `tests/unit/dev_seed.py`. Its children carry no
# lifetime counters and its completions/claims lists are empty, so the fixtures
# are hermetic and do not depend on whatever state the local dev container
# happens to be in. Never read `dev/config/.storage/` at test time: the running
# container rewrites it.
SEED: dict[str, Any] = copy.deepcopy(_COMMITTED_SEED)

# Keys the cards index into. Ordering is irrelevant, membership is not.
OVERVIEW_PAYLOAD_KEYS = {
    "children",
    "chores",
    "points_icon",
    "points_name",
    "rewards",
    "todays_completions",
    "total_children",
    "total_chores",
    "total_chores_completed",
    "total_completions_all_time",
    "total_pending_completions",
    "total_points_available",
    "total_rewards",
}
REWARD_ROW_KEYS = {
    "assigned_to",
    "calculated_costs",
    "child_daily_points",
    "cost",
    "days_to_goal",
    "description",
    "icon",
    "id",
    "is_jackpot",
    "name",
    "override_point_value",
}
CHORE_ROW_KEYS = {
    "assigned_to",
    "completion_percentage_per_month",
    "completion_sound",
    "daily_limit",
    "id",
    "name",
    "points",
    "time_category",
}
PENDING_COMPLETION_ROW_KEYS = {
    "child_id",
    "child_name",
    "chore_id",
    "chore_name",
    "completed_at",
    "completion_id",
    "points",
    "time_category",
    "type",
}
PENDING_CLAIM_ROW_KEYS = {
    "child_id",
    "child_name",
    "claim_id",
    "claimed_at",
    "cost",
    "reward_id",
    "reward_name",
    "type",
}


@pytest.fixture
def seed_data() -> dict[str, Any]:
    """A private, mutable copy of the seed for a single test."""
    return copy.deepcopy(SEED)


@pytest.fixture
def setup_choremander(hass: HomeAssistant, hass_storage: dict, bypass_frontend_registration):
    """Return a coroutine that seeds storage and sets up a config entry."""

    async def _setup(
        data: dict[str, Any],
        *,
        entry_data: dict[str, Any] | None = None,
    ) -> ConfigEntry:
        hass_storage[STORE_KEY] = {
            "version": 1,
            "minor_version": 1,
            "key": STORE_KEY,
            "data": data,
        }
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Choremander",
            data=(
                {"points_name": "Stars", "points_icon": "mdi:star"}
                if entry_data is None
                else entry_data
            ),
            entry_id=ENTRY_ID,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    return _setup


@pytest.fixture
async def seeded_entry(setup_choremander, seed_data: dict[str, Any]) -> ConfigEntry:
    """The dev seed set up as a live config entry."""
    return await setup_choremander(seed_data)


@pytest.fixture
def seeded_coordinator(hass: HomeAssistant, seeded_entry: ConfigEntry) -> ChoremanderCoordinator:
    """The live coordinator over the dev seed."""
    return hass.data[DOMAIN][ENTRY_ID]


def _pending_completion(**overrides: Any) -> dict[str, Any]:
    """Build a stored, unapproved completion row."""
    row = {
        "chore_id": "chore_homework",
        "child_id": "child_krishna",
        "completed_at": "2026-08-01T19:42:43.589634Z",
        "approved": False,
        "approved_at": None,
        "points_awarded": 0,
        "id": "comp_pending",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Overview sensor
# ---------------------------------------------------------------------------


async def test_overview_state_is_the_child_count_and_the_payload_keys_are_frozen(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """The overview sensor's state counts children and its key set is a contract."""
    state = hass.states.get(OVERVIEW)
    assert state is not None, f"{OVERVIEW} was never created by the sensor platform"
    assert state.state == "2", (
        "the overview state is the number of children; the seed has Krishna and Radha, "
        f"so it must read '2' but read {state.state!r}"
    )

    payload_keys = set(state.attributes) - {"friendly_name", "icon"}
    assert payload_keys == OVERVIEW_PAYLOAD_KEYS, (
        "the Lovelace cards index the overview attributes by name; added or renamed "
        f"keys break them silently. Unexpected: {sorted(payload_keys - OVERVIEW_PAYLOAD_KEYS)}, "
        f"missing: {sorted(OVERVIEW_PAYLOAD_KEYS - payload_keys)}"
    )
    assert state.attributes["icon"] == "mdi:clipboard-check-multiple"


async def test_overview_reports_seed_totals_and_one_row_per_child(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """Top-level counters and the `children` rows the points card renders."""
    attrs = hass.states.get(OVERVIEW).attributes

    assert attrs["total_children"] == 2
    assert attrs["total_chores"] == 8
    assert attrs["total_rewards"] == 5
    assert attrs["total_points_available"] == 40, (
        "total_points_available is the sum of current balances: Krishna 25 + Radha 15"
    )
    assert attrs["total_chores_completed"] == 0
    assert attrs["todays_completions"] == []
    assert attrs["total_completions_all_time"] == 0
    assert attrs["total_pending_completions"] == 0
    assert attrs["points_name"] == "Stars"
    assert attrs["points_icon"] == "mdi:star"

    assert attrs["children"] == [
        {
            "id": "child_krishna",
            "name": "Krishna",
            "points": 25,
            "pending_points": 0,
            "chore_order": [],
        },
        {
            "id": "child_radha",
            "name": "Radha",
            "points": 15,
            "pending_points": 0,
            "chore_order": [],
        },
    ], (
        "each child row must carry exactly id/name/points/pending_points/chore_order "
        "in seed order; streak and lifetime figures belong on the streaks sensor, "
        "because this payload has the least room under the 16KiB attribute ceiling"
    )


async def test_overview_reward_rows_carry_eleven_keys_and_dynamic_per_child_costs(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """`calculated_costs` is what the rewards card prices everything from."""
    rewards = {r["id"]: r for r in hass.states.get(OVERVIEW).attributes["rewards"]}
    assert set(rewards) == {
        "reward_screen",
        "reward_krishna_game",
        "reward_radha_book",
        "reward_movie_night",
        "reward_zoo_trip",
    }

    for reward_id, row in rewards.items():
        assert set(row) == REWARD_ROW_KEYS, (
            f"reward row {reward_id} must expose exactly the 11 keys the rewards card "
            f"reads; got {sorted(set(row) ^ REWARD_ROW_KEYS)} as the difference"
        )

    # Non-jackpot, dynamic: 15.0/day x 7 days = 105 for Krishna, 13.0 x 7 = 91 for Radha.
    assert rewards["reward_screen"]["calculated_costs"] == {
        "child_krishna": 105,
        "child_radha": 91,
    }, "screen time is priced per child from each child's own assigned chores"
    assert rewards["reward_screen"]["cost"] == 10, (
        "the static `cost` is still published untouched next to the dynamic price"
    )

    # override_point_value short-circuits to the manual cost, for the assigned child only.
    assert rewards["reward_krishna_game"]["calculated_costs"] == {"child_krishna": 50}
    assert rewards["reward_radha_book"]["calculated_costs"] == {"child_radha": 30}

    # Jackpots pool every child's daily rate: (15.0 + 13.0) x 14 = 392, x 30 = 840.
    assert rewards["reward_movie_night"]["calculated_costs"] == {
        "child_krishna": 392,
        "child_radha": 392,
    }, "a jackpot charges every child the same pooled price"
    assert rewards["reward_zoo_trip"]["calculated_costs"] == {
        "child_krishna": 840,
        "child_radha": 840,
    }


async def test_child_daily_points_is_populated_only_for_jackpot_rewards(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """The weighted jackpot meter needs per-child rates; other rewards get `{}`."""
    rewards = {r["id"]: r for r in hass.states.get(OVERVIEW).attributes["rewards"]}

    for reward_id in ("reward_screen", "reward_krishna_game", "reward_radha_book"):
        assert rewards[reward_id]["child_daily_points"] == {}, (
            f"{reward_id} is not a jackpot, so the sensor must not compute the weighted "
            "segment data the jackpot meter uses"
        )

    for reward_id in ("reward_movie_night", "reward_zoo_trip"):
        assert rewards[reward_id]["child_daily_points"] == {
            "child_krishna": 15.0,
            "child_radha": 13.0,
        }, f"{reward_id} is a jackpot, so each child's unrounded daily rate is published"


async def test_overview_chore_rows_carry_eight_keys_with_defaults_materialised(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """Legacy seed rows gain sound/percentage defaults on the way out."""
    chores = {c["id"]: c for c in hass.states.get(OVERVIEW).attributes["chores"]}
    assert len(chores) == 8

    for chore_id, row in chores.items():
        assert set(row) == CHORE_ROW_KEYS, (
            f"chore row {chore_id} must expose exactly the 8 keys the child card reads; "
            f"difference was {sorted(set(row) ^ CHORE_ROW_KEYS)}"
        )

    assert chores["chore_bed"] == {
        "id": "chore_bed",
        "name": "Make Bed",
        "points": 2,
        "time_category": "morning",
        "daily_limit": 1,
        "assigned_to": [],
        "completion_sound": "coin",
        "completion_percentage_per_month": 100,
    }, (
        "the seed row for Make Bed stores neither completion_sound nor "
        "completion_percentage_per_month; the model defaults must surface as 'coin' and 100"
    )


async def test_pending_points_sum_the_chore_points_of_unapproved_completions(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """`pending_points` drives the "waiting stars" figure on the child card."""
    seed_data["completions"] = [
        _pending_completion(),  # chore_homework -> 5 points
        _pending_completion(id="comp_orphan", chore_id="chore_deleted"),
    ]
    await setup_choremander(seed_data)

    attrs = hass.states.get(OVERVIEW).attributes
    krishna, radha = attrs["children"]

    assert krishna["pending_points"] == 5, (
        "Do Homework is worth 5 and is awaiting approval, so Krishna has 5 stars waiting; "
        "the completion pointing at a deleted chore must contribute nothing"
    )
    assert radha["pending_points"] == 0
    assert attrs["total_pending_completions"] == 2, (
        "the orphaned completion is excluded from pending_points but still counted"
    )


# ---------------------------------------------------------------------------
# Pending approvals sensor
# ---------------------------------------------------------------------------


async def test_pending_approvals_state_counts_and_item_shape(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """The approvals card iterates `chore_completions` and needs every key."""
    seed_data["completions"] = [_pending_completion()]
    await setup_choremander(seed_data)

    state = hass.states.get(PENDING_APPROVALS)
    assert state is not None, f"{PENDING_APPROVALS} was never created"
    assert state.state == "1", (
        "one unapproved chore completion and no reward claims means one pending approval"
    )

    payload_keys = set(state.attributes) - {"friendly_name", "icon"}
    assert payload_keys == {
        "chore_completions",
        "pending_chore_completions",
        "pending_reward_claims",
        "reward_claims",
        "state_class",
    }, "the approvals card reads these attribute names verbatim"

    assert state.attributes["pending_chore_completions"] == 1
    assert state.attributes["pending_reward_claims"] == 0
    assert state.attributes["reward_claims"] == []

    (item,) = state.attributes["chore_completions"]
    assert set(item) == PENDING_COMPLETION_ROW_KEYS, (
        "the approve/reject buttons read `completion_id` (NOT `id`) off each row; "
        f"difference was {sorted(set(item) ^ PENDING_COMPLETION_ROW_KEYS)}"
    )
    assert item["completion_id"] == "comp_pending"
    assert item["type"] == "chore"
    assert item["child_id"] == "child_krishna"
    assert item["child_name"] == "Krishna"
    assert item["chore_id"] == "chore_homework"
    assert item["chore_name"] == "Do Homework"
    assert item["points"] == 5
    assert item["time_category"] == "evening"


async def test_pending_approvals_timestamps_use_an_offset_suffix_not_z(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """The sensor calls `.isoformat()` directly, unlike everything that is stored."""
    seed_data["completions"] = [_pending_completion()]
    await setup_choremander(seed_data)

    (item,) = hass.states.get(PENDING_APPROVALS).attributes["chore_completions"]
    assert item["completed_at"] == "2026-08-01T19:42:43.589634+00:00", (
        "stored timestamps are normalised to a 'Z' suffix by models.format_datetime, but "
        "PendingApprovalsSensor bypasses it and emits a '+00:00' offset; any consumer "
        "matching on 'Z' will miss"
    )


async def test_pending_approvals_counts_orphans_but_omits_them_from_the_detail_lists(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """Counters and detail rows deliberately disagree when a target is deleted."""
    seed_data["completions"] = [
        _pending_completion(),
        _pending_completion(id="comp_orphan", chore_id="chore_deleted"),
    ]
    seed_data["reward_claims"] = [
        {
            "reward_id": "reward_deleted",
            "child_id": "child_krishna",
            "claimed_at": "2026-08-01T20:00:00Z",
            "approved": False,
            "approved_at": None,
            "id": "claim_orphan",
        }
    ]
    await setup_choremander(seed_data)

    state = hass.states.get(PENDING_APPROVALS)
    assert state.state == "3", "the state counts raw pending rows: 2 completions + 1 claim"
    assert state.attributes["pending_chore_completions"] == 2
    assert state.attributes["pending_reward_claims"] == 1

    assert [i["completion_id"] for i in state.attributes["chore_completions"]] == [
        "comp_pending"
    ], "a completion whose chore was deleted cannot be rendered, so it is dropped from the list"
    assert state.attributes["reward_claims"] == [], (
        "a claim whose reward was deleted is dropped too, leaving a count with nothing to show"
    )


async def test_pending_reward_claim_rows_carry_the_claim_payload_keys(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """`reward_claims` rows key on `claim_id`, mirroring `completion_id`."""
    seed_data["reward_claims"] = [
        {
            "reward_id": "reward_screen",
            "child_id": "child_radha",
            "claimed_at": "2026-08-01T20:15:00Z",
            "approved": False,
            "approved_at": None,
            "id": "claim_1",
        }
    ]
    await setup_choremander(seed_data)

    (item,) = hass.states.get(PENDING_APPROVALS).attributes["reward_claims"]
    assert set(item) == PENDING_CLAIM_ROW_KEYS, (
        f"difference was {sorted(set(item) ^ PENDING_CLAIM_ROW_KEYS)}"
    )
    assert item["claim_id"] == "claim_1"
    assert item["type"] == "reward"
    assert item["child_name"] == "Radha"
    assert item["reward_name"] == "30 Min Screen Time"
    assert item["cost"] == 10, (
        "the claim row publishes the STATIC reward cost, not the dynamic 91 Radha was "
        "actually charged"
    )
    assert item["claimed_at"] == "2026-08-01T20:15:00+00:00"


# ---------------------------------------------------------------------------
# Today's completions / timezone
# ---------------------------------------------------------------------------


async def test_todays_completions_are_filtered_by_the_local_calendar_date(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any], freezer
) -> None:
    """ "Today" is the HA-local day, not the UTC day, and includes pending rows."""
    await hass.config.async_set_time_zone("America/Los_Angeles")
    seed_data["completions"] = [
        {
            "chore_id": "chore_bed",
            "child_id": "child_krishna",
            # 2026-08-01 22:00 PDT - the previous local day.
            "completed_at": "2026-08-02T05:00:00Z",
            "approved": True,
            "approved_at": "2026-08-02T05:00:00Z",
            "points_awarded": 2,
            "id": "comp_late_last_night",
        },
        {
            "chore_id": "chore_teeth_am",
            "child_id": "child_radha",
            # 2026-08-02 11:00 PDT.
            "completed_at": "2026-08-02T18:00:00Z",
            "approved": True,
            "approved_at": "2026-08-02T18:00:00Z",
            "points_awarded": 1,
            "id": "comp_today_approved",
        },
        {
            "chore_id": "chore_homework",
            "child_id": "child_krishna",
            # 2026-08-02 12:00 PDT, still awaiting approval.
            "completed_at": "2026-08-02T19:00:00Z",
            "approved": False,
            "approved_at": None,
            "points_awarded": 0,
            "id": "comp_today_pending",
        },
    ]
    entry = await setup_choremander(seed_data)
    coordinator: ChoremanderCoordinator = hass.data[DOMAIN][ENTRY_ID]
    overview = ChoremandorOverallStatsSensor(coordinator, entry)

    freezer.move_to("2026-08-02T18:30:00+00:00")  # 11:30 PDT on Aug 2
    rows = overview.extra_state_attributes["todays_completions"]
    assert [r["completion_id"] for r in rows] == [
        "comp_today_approved",
        "comp_today_pending",
    ], (
        "at 11:30 local on Aug 2 only the two Aug-2-local completions count, and an "
        "unapproved one is included just like an approved one"
    )
    assert set(rows[0]) == {
        "completion_id",
        "chore_id",
        "child_id",
        "approved",
        "completed_at",
    }, "each today's-completion row has exactly these five keys"
    assert [r["approved"] for r in rows] == [True, False]

    freezer.move_to("2026-08-02T06:00:00+00:00")  # 23:00 PDT on Aug 1
    rows = overview.extra_state_attributes["todays_completions"]
    assert [r["completion_id"] for r in rows] == ["comp_late_last_night"], (
        "the same UTC-stamped rows land on a different local day; a UTC-based filter "
        "would wrongly return the Aug-2 rows here"
    )


# ---------------------------------------------------------------------------
# Per-child sensors
# ---------------------------------------------------------------------------


async def test_child_points_sensor_publishes_balance_and_lifetime_stats(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """The points sensor's state is the spendable balance."""
    state = hass.states.get("sensor.krishna_points")
    assert state is not None, "sensor.krishna_points was never created"
    assert state.state == "25", "Krishna's seeded balance is 25 stars"
    assert state.attributes["unit_of_measurement"] == "Stars"
    assert state.attributes["icon"] == "mdi:star"
    assert state.attributes["state_class"] == "total"

    payload_keys = set(state.attributes) - {
        "friendly_name",
        "icon",
        "unit_of_measurement",
        "state_class",
    }
    assert payload_keys == {
        "child_id",
        "child_name",
        "avatar",
        "total_points_earned",
        "total_chores_completed",
        "current_streak",
        "best_streak",
    }
    assert state.attributes["child_id"] == "child_krishna"
    assert state.attributes["child_name"] == "Krishna"
    assert state.attributes["current_streak"] == 0
    assert state.attributes["best_streak"] == 0


async def test_child_stats_assigned_chores_match_the_button_platform_filter(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """A child's chore list must agree with the buttons actually created for her."""
    state = hass.states.get("sensor.radha_stats")
    assert state is not None, "sensor.radha_stats was never created"
    assert state.state == "0", "the stats state is lifetime chores completed"
    assert state.attributes["points"] == 15
    assert state.attributes["chore_order"] == []

    assigned = state.attributes["assigned_chores"]
    assert [c["id"] for c in assigned] == [
        "chore_bed",
        "chore_teeth_am",
        "chore_water_plants",
        "chore_practice_piano",
        "chore_teeth_pm",
        "chore_tidy_room",
    ], "Radha gets the unassigned chores plus her own; Krishna-only chores are excluded"
    for row in assigned:
        assert set(row) == {"id", "name", "points", "time_category"}, (
            "assigned_chores rows are deliberately narrower than the overview chore rows"
        )

    registry = er.async_get(hass)
    button_chore_ids = {
        entity.unique_id[len(f"{ENTRY_ID}_child_radha_") : -len("_complete")]
        for entity in er.async_entries_for_config_entry(registry, ENTRY_ID)
        if entity.domain == "button"
        and entity.unique_id.startswith(f"{ENTRY_ID}_child_radha_")
        and entity.unique_id.endswith("_complete")
    }
    assert button_chore_ids == {c["id"] for c in assigned}, (
        "the stats sensor and the button platform apply the same assignment filter; if "
        "they drift, the child card lists chores that have no button behind them"
    )


async def test_a_new_child_gains_exactly_two_sensors_and_gains_them_only_once(
    hass: HomeAssistant, seeded_entry: ConfigEntry, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """The coordinator listener backfills sensors for children added after setup."""
    batches: list[list] = []

    def collect(new_entities, update_before_add: bool = False) -> None:
        batches.append(list(new_entities))

    await sensor_platform.async_setup_entry(hass, seeded_entry, collect)
    assert len(batches) == 1, "setup adds every entity in a single call"
    assert len(batches[0]) == 9, (
        "the seed yields 1 overview + 2 children x 2 sensors + pending-approvals, "
        "activity, streaks and schedule sensors"
    )

    await seeded_coordinator.async_add_child("Mira")

    assert len(batches) == 2, "adding a child must trigger exactly one follow-up add"
    assert [type(e).__name__ for e in batches[1]] == [
        "ChildPointsSensor",
        "ChildStatsSensor",
    ], "a new child gets a points sensor and a stats sensor, in that order"
    assert [e.name for e in batches[1]] == ["Mira Points", "Mira Stats"]

    await seeded_coordinator.async_refresh()
    assert len(batches) == 2, (
        "later refreshes must not re-add sensors for a child that is already tracked"
    )


async def test_sensors_for_a_removed_child_degrade_to_zero_and_empty_attributes(
    hass: HomeAssistant, seeded_entry: ConfigEntry, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """Deleting a child leaves its entities alive but inert."""
    radha = seeded_coordinator.get_child("child_radha")
    points = ChildPointsSensor(seeded_coordinator, seeded_entry, radha)
    stats = ChildStatsSensor(seeded_coordinator, seeded_entry, radha)

    await seeded_coordinator.async_remove_child("child_radha")

    assert points.native_value == 0, "a deleted child's balance reads 0, not the last value"
    assert points.extra_state_attributes == {}
    assert stats.native_value == 0
    assert stats.extra_state_attributes == {}
    assert stats.icon == "mdi:account-circle", "the avatar falls back once the child is gone"
    assert points.name == "Radha Points", (
        "the name is captured at construction time and is never re-derived, so the entity "
        "keeps identifying the deleted child"
    )


# ---------------------------------------------------------------------------
# Fallbacks and robustness
# ---------------------------------------------------------------------------


async def test_cost_calculation_failure_falls_back_to_the_static_reward_cost(
    hass: HomeAssistant, seeded_entry: ConfigEntry, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """A raising pricing engine must not blank out the rewards card."""
    overview = ChoremandorOverallStatsSensor(seeded_coordinator, seeded_entry)

    # patch.object keeps the attribute present, so the sensor's `hasattr` gate still
    # passes and the try/except fallback is genuinely reachable.
    with patch.object(
        seeded_coordinator,
        "calculate_dynamic_reward_costs",
        side_effect=RuntimeError("pricing exploded"),
    ):
        rewards = {r["id"]: r for r in overview.extra_state_attributes["rewards"]}

    assert rewards["reward_screen"]["calculated_costs"] == {
        "child_krishna": 10,
        "child_radha": 10,
    }, "an unassigned reward falls back to its static cost for every child"
    assert rewards["reward_krishna_game"]["calculated_costs"] == {"child_krishna": 50}, (
        "an assigned reward falls back to its static cost for its assigned children only"
    )
    assert rewards["reward_movie_night"]["child_daily_points"] == {
        "child_krishna": 15.0,
        "child_radha": 13.0,
    }, "the jackpot weighting is computed separately and survives a pricing failure"


async def test_a_jackpot_weighting_failure_leaves_child_daily_points_empty(
    hass: HomeAssistant, seeded_entry: ConfigEntry, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """`get_child_daily_points` has its own guard, separate from the pricing one."""
    overview = ChoremandorOverallStatsSensor(seeded_coordinator, seeded_entry)

    with patch.object(
        seeded_coordinator,
        "get_child_daily_points",
        side_effect=RuntimeError("weighting exploded"),
    ):
        rewards = {r["id"]: r for r in overview.extra_state_attributes["rewards"]}

    assert rewards["reward_movie_night"]["child_daily_points"] == {}, (
        "a jackpot reward whose weighting blew up publishes an empty dict rather "
        "than dropping the reward or raising into the state machine"
    )
    assert rewards["reward_movie_night"]["calculated_costs"] == {
        "child_krishna": 392,
        "child_radha": 392,
    }, "pricing is unaffected - the two calculations are independently guarded"
    assert rewards["reward_screen"]["child_daily_points"] == {}, (
        "a non-jackpot reward never asks for the weighting in the first place"
    )


async def test_a_coordinator_without_a_pricing_method_uses_the_static_cost(
    hass: HomeAssistant, seeded_entry: ConfigEntry, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """The `hasattr` gate exists for coordinators that predate dynamic pricing."""
    overview = ChoremandorOverallStatsSensor(seeded_coordinator, seeded_entry)

    real = type(seeded_coordinator).calculate_dynamic_reward_costs
    try:
        del type(seeded_coordinator).calculate_dynamic_reward_costs
        assert not hasattr(seeded_coordinator, "calculate_dynamic_reward_costs")
        rewards = {r["id"]: r for r in overview.extra_state_attributes["rewards"]}
    finally:
        type(seeded_coordinator).calculate_dynamic_reward_costs = real

    assert rewards["reward_screen"]["calculated_costs"] == {
        "child_krishna": 10,
        "child_radha": 10,
    }, "with no pricing method at all every child is quoted the static cost"
    assert rewards["reward_krishna_game"]["calculated_costs"] == {"child_krishna": 50}, (
        "an assigned reward still quotes only its assigned children"
    )


async def test_points_name_and_icon_fall_back_when_missing_from_coordinator_data(
    hass: HomeAssistant, seeded_entry: ConfigEntry, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """All three read sites default to Stars / mdi:star."""
    seeded_coordinator.data.pop("points_name")
    seeded_coordinator.data.pop("points_icon")

    overview = ChoremandorOverallStatsSensor(seeded_coordinator, seeded_entry)
    attrs = overview.extra_state_attributes
    assert attrs["points_name"] == "Stars"
    assert attrs["points_icon"] == "mdi:star"

    points = ChildPointsSensor(
        seeded_coordinator, seeded_entry, seeded_coordinator.get_child("child_krishna")
    )
    assert points.native_unit_of_measurement == "Stars"
    assert points.icon == "mdi:star"


async def test_a_renamed_points_currency_only_reaches_the_sensors_after_a_refresh(
    hass: HomeAssistant, setup_choremander, seed_data: dict[str, Any]
) -> None:
    """Renaming the currency flows to every card surface - one refresh late.

    CURRENT BEHAVIOUR: `async_setup_entry` writes the entry's points settings into
    storage *after* the coordinator's initial refresh and never refreshes again, so
    the freshly created sensors publish the pre-rename currency until the next
    30-second poll. This test pins today's behaviour so a fix is deliberate.
    """
    await setup_choremander(
        seed_data, entry_data={"points_name": "Coins", "points_icon": "mdi:cash"}
    )
    coordinator: ChoremanderCoordinator = hass.data[DOMAIN][ENTRY_ID]

    assert coordinator.storage.get_points_name() == "Coins", (
        "the entry's points settings are persisted to storage during setup"
    )
    assert hass.states.get(OVERVIEW).attributes["points_name"] == "Stars", (
        "but the coordinator snapshot the sensors read was taken before that write"
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    overview_attrs = hass.states.get(OVERVIEW).attributes
    assert overview_attrs["points_name"] == "Coins"
    assert overview_attrs["points_icon"] == "mdi:cash"

    points_state = hass.states.get("sensor.krishna_points")
    assert points_state.attributes["unit_of_measurement"] == "Coins", (
        "the child points sensor's unit is the configured currency name"
    )
    assert points_state.attributes["icon"] == "mdi:cash"


async def test_a_non_list_assigned_to_is_published_as_an_empty_list(
    hass: HomeAssistant, setup_choremander
) -> None:
    """Corrupt storage must not put a bare string where the cards expect a list."""
    data = {
        "children": [
            {
                "name": "Ada",
                "avatar": "mdi:account-circle",
                "points": 4,
                "total_points_earned": 0,
                "total_chores_completed": 0,
                "current_streak": 0,
                "best_streak": 0,
                "pending_rewards": [],
                "chore_order": [],
                "id": "child_ada",
            }
        ],
        # assigned_to stored as a bare string instead of a list.
        "chores": [
            {
                "id": "chore_odd",
                "name": "Odd Chore",
                "points": 3,
                "time_category": "anytime",
                "assigned_to": "child_ada",
                "requires_approval": False,
                "daily_limit": 1,
            }
        ],
        # assigned_to stored as null.
        "rewards": [
            {
                "id": "reward_odd",
                "name": "Odd Reward",
                "cost": 7,
                "icon": "mdi:gift",
                "assigned_to": None,
                "is_jackpot": False,
                "override_point_value": True,
            }
        ],
        "completions": [],
        "reward_claims": [],
        "points_name": "Stars",
        "points_icon": "mdi:star",
    }
    await setup_choremander(data)

    attrs = hass.states.get(OVERVIEW).attributes
    assert attrs["chores"][0]["assigned_to"] == [], (
        "a string assigned_to must be coerced to [] so the card's array handling holds"
    )
    assert attrs["rewards"][0]["assigned_to"] == [], (
        "a null assigned_to must be coerced to [] rather than published as None"
    )


# ---------------------------------------------------------------------------
# Registry-level identity
# ---------------------------------------------------------------------------


async def test_unique_ids_follow_the_documented_entry_scoped_format(
    hass: HomeAssistant, seeded_entry: ConfigEntry, seeded_coordinator: ChoremanderCoordinator
) -> None:
    """Unique ids are the entity registry's identity - changing one orphans the entity."""
    registry = er.async_get(hass)
    assert registry.async_get(OVERVIEW).unique_id == f"{ENTRY_ID}_overall_stats"
    assert registry.async_get(PENDING_APPROVALS).unique_id == f"{ENTRY_ID}_pending_approvals"
    assert registry.async_get("sensor.krishna_points").unique_id == (
        f"{ENTRY_ID}_child_krishna_points"
    )
    assert registry.async_get("sensor.krishna_stats").unique_id == (
        f"{ENTRY_ID}_child_krishna_stats"
    )

    # Same formats straight off freshly constructed entities.
    krishna = seeded_coordinator.get_child("child_krishna")
    assert (
        ChoremandorOverallStatsSensor(seeded_coordinator, seeded_entry).unique_id
        == f"{ENTRY_ID}_overall_stats"
    )
    assert (
        PendingApprovalsSensor(seeded_coordinator, seeded_entry).unique_id
        == f"{ENTRY_ID}_pending_approvals"
    )
    assert (
        ChildPointsSensor(seeded_coordinator, seeded_entry, krishna).unique_id
        == f"{ENTRY_ID}_child_krishna_points"
    )
    assert (
        ChildStatsSensor(seeded_coordinator, seeded_entry, krishna).unique_id
        == f"{ENTRY_ID}_child_krishna_stats"
    )


async def test_every_sensor_hangs_off_the_single_choremander_device(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """One device per config entry keeps the integration page coherent."""
    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, ENTRY_ID)})
    assert device is not None, "no device was registered for the Choremander config entry"
    assert device.name == "Choremander"
    assert device.manufacturer == "Choremander"
    assert device.model == "Family Chore Manager"

    registry = er.async_get(hass)
    sensor_entities = [
        entity
        for entity in er.async_entries_for_config_entry(registry, ENTRY_ID)
        if entity.domain == "sensor"
    ]
    assert len(sensor_entities) == 9, (
        "the seed yields overview + pending approvals + activity + streaks + "
        "schedule + 2 sensors for each of 2 children"
    )
    assert {entity.device_id for entity in sensor_entities} == {device.id}, (
        "every sensor must be attached to the one Choremander device"
    )
