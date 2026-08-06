"""Tests for `custom_components.choremander.models`.

`models.py` imports nothing from Home Assistant, so every test here is a plain
synchronous unit test over real dataclasses - no `hass`, no mocks.

Two behaviours deserve the attention they get below:

* `Reward.from_dict` **inverts** the constructor default. `Reward(name="x")`
  gets `override_point_value=False` (dynamic pricing), but
  `Reward.from_dict({"name": "x"})` gets `True` (manual pricing), because the
  absent key is treated as legacy data that predates the field.
* `parse_datetime` and `format_datetime` are asymmetric: parsing preserves a
  non-UTC offset, formatting normalises everything to UTC with a `Z` suffix.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta, timezone
import json
import re

import pytest

from custom_components.choremander.models import (
    Child,
    Chore,
    ChoreCompletion,
    Reward,
    RewardClaim,
    format_datetime,
    generate_id,
    parse_datetime,
)
from tests.unit.dev_seed import DEV_SEED

EDT = timezone(timedelta(hours=-4))


def _dev_seed_row(collection: str, row_id: str) -> dict:
    """Return one row from the committed dev-container seed.

    Read from `tests/unit/dev_seed.py`, not from `dev/config/.storage/`: the
    running dev container rewrites that file underneath the suite.
    """
    data = copy.deepcopy(DEV_SEED)
    for row in data[collection]:
        if row.get("id") == row_id:
            return row
    pytest.skip(f"dev seed has no {collection} row {row_id!r}")


# --------------------------------------------------------------------------
# Reward.from_dict - the is_dynamic migration
# --------------------------------------------------------------------------


def test_reward_from_dict_defaults_to_manual_pricing_opposite_of_the_constructor() -> None:
    """A dict without the key means legacy data, so pricing stays manual."""
    assert Reward.from_dict({"name": "x"}).override_point_value is True, (
        "a reward loaded from storage without override_point_value predates dynamic "
        "pricing, so from_dict must fall back to the manual cost"
    )
    assert Reward(name="x").override_point_value is False, (
        "a freshly constructed Reward defaults to dynamic pricing - from_dict "
        "deliberately disagrees with the constructor and this asymmetry is load-bearing"
    )


def test_reward_from_dict_migrates_legacy_is_dynamic_in_both_directions() -> None:
    """`is_dynamic` is the logical inverse of `override_point_value`."""
    assert Reward.from_dict({"name": "x", "is_dynamic": True}).override_point_value is False, (
        "a legacy dynamic reward must keep calculating its cost from chore points"
    )
    assert Reward.from_dict({"name": "x", "is_dynamic": False}).override_point_value is True, (
        "a legacy non-dynamic reward must keep using its manually entered cost"
    )
    assert (
        Reward.from_dict(
            {"name": "x", "override_point_value": False, "is_dynamic": True}
        ).override_point_value
        is False
    ), "an explicit override_point_value must win over the legacy is_dynamic field"


def test_reward_from_dict_treats_explicit_null_override_as_legacy_data() -> None:
    """The migration triggers on a `None` *value*, not on a missing key."""
    migrated = Reward.from_dict({"name": "x", "override_point_value": None, "is_dynamic": True})
    assert migrated.override_point_value is False, (
        "override_point_value=None must be treated as unset and fall through to the "
        "is_dynamic migration, because the guard is `is None` rather than key presence"
    )


def test_reward_to_dict_never_emits_is_dynamic_and_round_trips_stably() -> None:
    """The migration is one-way: `is_dynamic` is dropped on the first save."""
    stored = Reward.from_dict({"name": "Movie", "id": "r1", "is_dynamic": True}).to_dict()

    assert "is_dynamic" not in stored, (
        "to_dict must not re-emit the legacy field, otherwise it would be migrated "
        f"again on every load; got keys {sorted(stored)}"
    )
    assert stored["override_point_value"] is False
    assert Reward.from_dict(stored).to_dict() == stored, (
        "a second load/save cycle must be a no-op once the legacy field is gone"
    )


# --------------------------------------------------------------------------
# Shape of the five models
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "instance", "expected_key_count"),
    [
        (Child, Child(name="Krishna"), 10),
        (Chore, Chore(name="Make Bed"), 16),
        (Reward, Reward(name="Screen Time"), 9),
        (
            ChoreCompletion,
            ChoreCompletion(
                chore_id="c1",
                child_id="k1",
                completed_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
            ),
            7,
        ),
        (
            RewardClaim,
            RewardClaim(
                reward_id="r1",
                child_id="k1",
                claimed_at=datetime(2026, 8, 1, 12, tzinfo=UTC),
            ),
            6,
        ),
    ],
)
def test_to_dict_emits_exactly_the_dataclass_fields(
    model: type, instance: object, expected_key_count: int
) -> None:
    """A field added to a dataclass but not to `to_dict` is silently never saved."""
    from dataclasses import fields

    field_names = {f.name for f in fields(model)}
    stored = instance.to_dict()

    assert set(stored) == field_names, (
        f"{model.__name__}.to_dict() must persist every dataclass field; missing "
        f"{sorted(field_names - set(stored))}, unexpected {sorted(set(stored) - field_names)}"
    )
    assert len(stored) == expected_key_count, (
        f"{model.__name__} changed shape: expected {expected_key_count} stored keys, "
        f"got {len(stored)} ({sorted(stored)})"
    )


@pytest.mark.parametrize(
    "instance",
    [
        Child(name="Krishna", points=25, pending_rewards=["r1"], chore_order=["c1"], id="k1"),
        Chore(name="Make Bed", points=2, assigned_to=["k1"], due_days=["monday"], id="c1"),
        Reward(name="Screen Time", cost=10, assigned_to=["k1"], days_to_goal=7, id="r1"),
        ChoreCompletion(
            chore_id="c1",
            child_id="k1",
            completed_at=datetime(2026, 8, 1, 19, 42, 43, 589634, tzinfo=UTC),
            approved=True,
            approved_at=datetime(2026, 8, 1, 19, 43, tzinfo=UTC),
            points_awarded=2,
            id="cmp1",
        ),
        RewardClaim(
            reward_id="r1",
            child_id="k1",
            claimed_at=datetime(2026, 8, 1, 19, 42, 43, 589634, tzinfo=UTC),
            approved=True,
            approved_at=datetime(2026, 8, 1, 19, 43, tzinfo=UTC),
            id="clm1",
        ),
    ],
    ids=["child", "chore", "reward", "completion", "claim"],
)
def test_save_then_load_returns_an_identical_object(instance: object) -> None:
    """Persisting and reloading must not alter a single field."""
    reloaded = type(instance).from_dict(instance.to_dict())

    assert reloaded == instance, (
        f"{type(instance).__name__} did not survive a to_dict/from_dict round trip: "
        f"{reloaded!r} != {instance!r}"
    )


def test_from_dict_aliases_the_caller_s_lists_instead_of_copying_them() -> None:
    """Mutating the source dict after loading mutates the model - and vice versa."""
    source = {"name": "Krishna", "pending_rewards": ["r1"], "chore_order": ["c1"]}
    child = Child.from_dict(source)

    assert child.pending_rewards is source["pending_rewards"], (
        "from_dict stores the caller's list object; a defensive copy here would be a "
        "behaviour change for every caller that mutates storage rows in place"
    )
    source["pending_rewards"].append("r2")
    assert child.pending_rewards == ["r1", "r2"], (
        "the aliasing must be observable in both directions"
    )


def test_default_list_fields_are_not_shared_between_instances() -> None:
    """`field(default_factory=list)` - two children must not share one list."""
    first = Child(name="Krishna")
    second = Child(name="Radha")

    first.pending_rewards.append("r1")
    first.chore_order.append("c1")

    assert second.pending_rewards == [], (
        "Radha inherited Krishna's pending rewards - the default list is shared"
    )
    assert second.chore_order == [], (
        "Radha inherited Krishna's chore order - the default list is shared"
    )


def test_from_dict_preserves_a_falsy_id_verbatim() -> None:
    """`data.get("id", generate_id())` only substitutes on a *missing* key."""
    assert Child.from_dict({"name": "x", "id": ""}).id == "", (
        "an empty stored id must be kept as-is, not regenerated; storage matches rows "
        "by id, so two blank ids collide rather than being repaired here"
    )
    assert Chore.from_dict({"name": "x", "id": ""}).id == ""
    assert Reward.from_dict({"name": "x", "id": ""}).id == ""


def test_models_apply_no_validation_to_their_inputs() -> None:
    """Validation lives in the options-flow selectors, never in the models."""
    chore = Chore.from_dict(
        {
            "name": "Nonsense",
            "points": "abc",
            "time_category": "banana",
            "daily_limit": -3,
            "completion_sound": "not-a-sound",
            "rotation": "banana",
            "due_days": ["funday"],
        }
    )
    reward = Reward.from_dict({"name": "Free", "cost": -10, "days_to_goal": 0})

    assert chore.points == "abc", "a non-numeric points value is stored verbatim"
    assert chore.time_category == "banana", "an unknown time category is stored verbatim"
    assert chore.daily_limit == -3, "a negative daily limit is stored verbatim"
    assert chore.completion_sound == "not-a-sound", "an unknown sound is stored verbatim"
    assert chore.rotation == "banana", (
        "an unknown rotation cadence is stored verbatim - schedule.py is what decides "
        "how to survive it, and it does so by falling back rather than by rejecting"
    )
    assert chore.due_days == ["funday"], "an unknown weekday is stored verbatim"
    assert reward.cost == -10, "a negative cost is stored verbatim"
    assert reward.days_to_goal == 0, "a zero days_to_goal is stored verbatim"


def test_to_dict_output_is_json_serialisable() -> None:
    """Storage writes these dicts straight to disk via the HA JSON store."""
    completion = ChoreCompletion(
        chore_id="c1",
        child_id="k1",
        completed_at=datetime(2026, 8, 1, 19, 42, 43, 589634, tzinfo=EDT),
        approved=True,
        approved_at=datetime(2026, 8, 1, 19, 43, tzinfo=UTC),
        points_awarded=2,
        id="cmp1",
    )

    encoded = json.loads(json.dumps(completion.to_dict()))

    assert encoded["completed_at"] == "2026-08-01T23:42:43.589634Z", (
        "datetimes must be pre-formatted as strings; a raw datetime would raise "
        "TypeError inside the HA store"
    )
    assert encoded["approved_at"] == "2026-08-01T19:43:00Z"


# --------------------------------------------------------------------------
# Legacy rows from the dev seed
# --------------------------------------------------------------------------


def test_chore_from_dict_fills_defaults_for_a_legacy_seed_row() -> None:
    """The seeded chores predate several fields; from_dict materialises them."""
    row = _dev_seed_row("chores", "chore_bed")
    assert "completion_percentage_per_month" not in row, (
        "this test only means something while the seed row is still missing the key"
    )

    chore = Chore.from_dict(row)

    assert chore.completion_percentage_per_month == 100, (
        "an absent completion rate must default to 100 (every day) - the dynamic "
        "pricing multiplier is completion_percentage_per_month / 100"
    )
    assert chore.completion_sound == "coin", "an absent sound must default to 'coin'"
    assert chore.description == "", "an absent description must default to an empty string"
    assert chore.due_days == [], "an absent due_days must default to an empty list"
    assert chore.name == "Make Bed"
    assert chore.points == 2
    assert chore.id == "chore_bed"


def test_a_chore_stored_before_scheduling_existed_loads_unscheduled() -> None:
    """This is what buys the feature its lack of a storage version bump.

    Every chore row on every existing install predates all five scheduling
    columns. They default here rather than in a migration, and the defaults
    have to spell "no schedule" exactly - an empty `due_days` meaning every day
    and a rotation of "none" meaning everyone assigned - or upgrading would
    quietly un-schedule the installed base.
    """
    chore = Chore.from_dict(
        {"name": "Make Bed", "points": 2, "assigned_to": ["k1"], "id": "chore_bed"}
    )

    assert chore.due_days == []
    assert chore.rotation == "none"
    assert chore.rotation_order == []
    assert chore.rotation_anchor == ""
    assert chore.day_overrides == {}
    assert chore.date_overrides == {}


def test_the_schedule_fields_survive_a_save_and_reload_intact() -> None:
    """Nested dicts included - they are the only non-flat values a chore stores."""
    chore = Chore(
        name="Dishes",
        due_days=["saturday", "sunday"],
        rotation="occurrence",
        rotation_order=["k1", "m1"],
        rotation_anchor="2026-08-08",
        day_overrides={"sunday": ["m1"], "monday": []},
        date_overrides={"2026-08-15": [], "2026-08-16": ["k1", "m1"]},
        assigned_to=["k1", "m1"],
        id="c1",
    )

    reloaded = Chore.from_dict(json.loads(json.dumps(chore.to_dict())))

    assert reloaded == chore, (
        "the round trip goes through JSON because that is what the HA store writes; "
        "a dict value that survived to_dict but not serialisation would still be a loss"
    )


def test_default_schedule_dicts_are_not_shared_between_chores() -> None:
    """`field(default_factory=dict)` - one family's override must not appear on
    every other chore in the house."""
    first = Chore(name="Dishes")
    second = Chore(name="Trash")

    first.day_overrides["saturday"] = ["k1"]
    first.date_overrides["2026-08-08"] = ["k1"]

    assert second.day_overrides == {}
    assert second.date_overrides == {}


def test_child_from_dict_zeroes_stats_absent_from_a_legacy_row() -> None:
    """A pre-stats child row loads with every counter at zero, not None."""
    child = Child.from_dict({"name": "Krishna", "points": 25, "id": "child_krishna"})

    assert child.points == 25, "an explicitly stored balance must be preserved"
    assert child.total_points_earned == 0
    assert child.total_chores_completed == 0
    assert child.current_streak == 0
    assert child.best_streak == 0
    assert child.pending_rewards == []
    assert child.chore_order == []
    assert child.avatar == "mdi:account-circle", (
        "an absent avatar must default to the one avatar guaranteed to be in "
        "AVATAR_OPTIONS, otherwise the child becomes un-editable in the options flow"
    )


# --------------------------------------------------------------------------
# parse_datetime / format_datetime
# --------------------------------------------------------------------------


def test_format_datetime_converts_an_offset_to_utc_with_a_z_suffix() -> None:
    """Everything is normalised to UTC on the way out."""
    formatted = format_datetime(datetime(2026, 8, 1, 15, 30, tzinfo=EDT))

    assert formatted == "2026-08-01T19:30:00Z", (
        "15:30-04:00 is 19:30 UTC and must be written with a Z suffix, not +00:00; "
        f"got {formatted!r}"
    )


def test_format_datetime_treats_a_naive_datetime_as_utc_without_shifting_it() -> None:
    """A naive datetime is stamped UTC, never reinterpreted in local time.

    Note: `parse_datetime`'s docstring claims naive values are assumed to be in
    the HA local timezone. The code does `replace(tzinfo=utc)`; the docstring is
    stale. This test pins the code.
    """
    assert format_datetime(datetime(2026, 8, 1, 15, 30)) == "2026-08-01T15:30:00Z", (
        "a naive datetime must keep its wall-clock time and simply gain a UTC stamp"
    )


def test_format_datetime_keeps_microseconds_and_omits_them_when_zero() -> None:
    """Timestamps are matched between optimistic and real completions to the ms."""
    with_micros = format_datetime(datetime(2026, 8, 1, 15, 30, 0, 123456, tzinfo=UTC))
    without_micros = format_datetime(datetime(2026, 8, 1, 15, 30, 0, 0, tzinfo=UTC))

    assert with_micros == "2026-08-01T15:30:00.123456Z", (
        f"microsecond precision must survive formatting; got {with_micros!r}"
    )
    assert without_micros == "2026-08-01T15:30:00Z", (
        "isoformat() omits a zero microsecond component, so stored strings are not a "
        f"fixed width; got {without_micros!r}"
    )


def test_format_datetime_returns_none_for_none() -> None:
    """`approved_at` is None until approval and must stay None in storage."""
    assert format_datetime(None) is None


def test_parse_datetime_preserves_a_non_utc_offset() -> None:
    """Parsing does *not* normalise - only formatting does."""
    parsed = parse_datetime("2026-08-01T15:30:00-04:00")

    assert parsed == datetime(2026, 8, 1, 15, 30, tzinfo=EDT)
    assert parsed.utcoffset() == timedelta(hours=-4), (
        "parse_datetime must hand back the original offset; it is asymmetric with "
        f"format_datetime, which converts to UTC. Got offset {parsed.utcoffset()}"
    )
    assert parsed.hour == 15, "the wall-clock hour must not be shifted on parse"


def test_parse_datetime_accepts_the_z_suffix_it_writes() -> None:
    """Storage round trips depend on `fromisoformat` accepting 'Z' (Python 3.11+)."""
    assert parse_datetime("2026-08-01T15:30:00Z") == datetime(2026, 8, 1, 15, 30, tzinfo=UTC), (
        "format_datetime writes a Z suffix, so parse_datetime must read one back"
    )


def test_parse_datetime_stamps_utc_on_naive_input() -> None:
    """Both the string and the datetime branches assume UTC for naive values."""
    from_string = parse_datetime("2026-08-01T15:30:00")
    from_datetime = parse_datetime(datetime(2026, 8, 1, 15, 30))

    assert from_string == datetime(2026, 8, 1, 15, 30, tzinfo=UTC)
    assert from_datetime == datetime(2026, 8, 1, 15, 30, tzinfo=UTC)


def test_parse_datetime_hands_back_an_aware_datetime_untouched() -> None:
    """An already-aware datetime is returned as the same object, offset intact."""
    aware = datetime(2026, 8, 1, 15, 30, tzinfo=EDT)

    assert parse_datetime(aware) is aware, (
        "parse_datetime must not copy or normalise an already-aware datetime; "
        "the caller's object is returned verbatim"
    )


def test_parse_datetime_returns_none_for_none_and_unsupported_types() -> None:
    """A garbage *type* is swallowed; a garbage *string* is not (see below)."""
    assert parse_datetime(None) is None
    assert parse_datetime(1234567890) is None, (  # type: ignore[arg-type]
        "a non-str, non-datetime value falls off the end of parse_datetime and "
        "returns None rather than raising"
    )


def test_parse_datetime_raises_on_a_malformed_iso_string() -> None:
    """A corrupt timestamp fails loudly - `from_dict` does not catch it."""
    with pytest.raises(ValueError):
        parse_datetime("garbage")

    with pytest.raises(ValueError, match="Invalid isoformat string"):
        ChoreCompletion.from_dict({"chore_id": "c1", "completed_at": "garbage"})

    with pytest.raises(ValueError, match="Invalid isoformat string"):
        RewardClaim.from_dict({"reward_id": "r1", "claimed_at": "garbage"})


def test_a_completion_without_a_timestamp_is_stamped_with_the_current_time(
    freezer,
) -> None:
    """A missing `completed_at` silently becomes "now" instead of failing."""
    freezer.move_to("2026-08-01T19:42:43+00:00")

    completion = ChoreCompletion.from_dict({"chore_id": "c1", "child_id": "k1"})
    claim = RewardClaim.from_dict({"reward_id": "r1", "child_id": "k1"})

    expected = datetime(2026, 8, 1, 19, 42, 43, tzinfo=UTC)
    assert completion.completed_at == expected, (
        "an absent completed_at must default to now in UTC, which means a corrupt row "
        "quietly counts against today's daily limit"
    )
    assert claim.claimed_at == expected
    assert completion.approved_at is None, "an absent approved_at must stay None"
    assert claim.approved_at is None


# --------------------------------------------------------------------------
# generate_id
# --------------------------------------------------------------------------


def test_generate_id_returns_eight_lowercase_hex_characters() -> None:
    """Ids are the first block of a uuid4, i.e. 32 bits of entropy."""
    for _ in range(100):
        generated = generate_id()
        assert re.fullmatch(r"[0-9a-f]{8}", generated), (
            f"generate_id() must return 8 lowercase hex characters; got {generated!r}"
        )


def test_generate_id_does_not_repeat_across_a_thousand_calls() -> None:
    """Storage matches rows by id, so a collision silently merges two records."""
    generated = [generate_id() for _ in range(1000)]

    assert len(set(generated)) == 1000, (
        "generate_id() produced a duplicate within 1000 calls. Note the id is only "
        "32 bits, so collisions become likely in the tens of thousands - this test "
        "guards the common case, not the birthday bound"
    )
