"""Unit tests for `ChoremanderStorage`.

`storage.py` is a thin, unvalidated CRUD layer over a single Home Assistant
`Store`. Two things in it are load-bearing and easy to break by accident:

* the one-shot `assigned_to` **name -> child id** migration that runs inside
  `async_load`, and
* the **duplicate-append hazard** - `update_child` / `update_chore` /
  `update_reward` silently *upsert* when the id is unknown, while
  `update_completion` / `update_reward_claim` silently *do nothing*, and no
  `add_*` method rejects a duplicate id.

Everything here exercises the real `Store` through
`pytest-homeassistant-custom-component`'s `hass_storage` fixture, so a save is
an observable event rather than a mock assertion.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from datetime import UTC, datetime
import inspect
import logging
from typing import Any

from homeassistant.core import HomeAssistant
import pytest

from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import (
    Child,
    Chore,
    ChoreCompletion,
    Reward,
    RewardClaim,
)
from custom_components.choremander.storage import (
    STORAGE_KEY,
    STORAGE_VERSION,
    ChoremanderStorage,
)

STORAGE_LOGGER = "custom_components.choremander.storage"

#: The exact shape `async_load` seeds when no store file exists.
DEFAULT_KEYS = {
    "children",
    "chores",
    "rewards",
    "completions",
    "reward_claims",
    "points_transactions",
    "points_name",
    "points_icon",
}


def _store_key(entry_id: str) -> str:
    """The on-disk store key Choremander uses for a config entry."""
    return f"{STORAGE_KEY}.{entry_id}"


def _seed_store(hass_storage: dict[str, Any], entry_id: str, data: dict[str, Any]) -> str:
    """Pre-populate the HA store file for `entry_id` with `data`."""
    key = _store_key(entry_id)
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": data,
    }
    return key


def _storage_log(caplog: pytest.LogCaptureFixture) -> str:
    """Only the messages `storage.py` itself emitted, ignoring HA's own noise."""
    return "\n".join(
        record.getMessage() for record in caplog.records if record.name == STORAGE_LOGGER
    )


def _track_saves(storage: ChoremanderStorage) -> list[None]:
    """Record every `async_save` call while still performing the real save.

    Returned list grows by one entry per persist. Install this *before*
    `async_load`, because the migration saves from inside the load.
    """
    saves: list[None] = []
    original: Callable[[], Any] = storage.async_save

    async def counting_save() -> None:
        saves.append(None)
        await original()

    storage.async_save = counting_save  # type: ignore[method-assign]
    return saves


# `dev_seed` (a deep copy of the committed dev-container seed) comes from
# tests/unit/conftest.py.


# ---------------------------------------------------------------------------
# async_load: defaults and identity
# ---------------------------------------------------------------------------


async def test_async_load_seeds_exactly_eight_default_keys_when_no_store_file(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A first-run install gets empty collections plus the Stars defaults."""
    storage = ChoremanderStorage(hass, "fresh_entry")

    data = await storage.async_load()

    assert set(data) == DEFAULT_KEYS, (
        f"a brand-new install must seed exactly the eight documented keys; got {sorted(data)}"
    )
    assert data["children"] == [] and data["chores"] == [] and data["rewards"] == []
    assert data["completions"] == [] and data["reward_claims"] == []
    assert data["points_transactions"] == [], "the ledger starts empty"
    assert data["points_name"] == "Stars", "default currency name is Stars"
    assert data["points_icon"] == "mdi:star", "default currency icon is mdi:star"
    assert "pending_completions" not in data and "pending_reward_claims" not in data, (
        "the seeded default must not invent the vestigial pending_* collections "
        "that only appear in legacy files"
    )
    assert data is storage.data, (
        "async_load must hand back the live dict, not a copy - callers mutate it "
        "in place and expect async_save to pick the change up"
    )
    assert _store_key("fresh_entry") not in hass_storage, (
        "seeding defaults must not write a store file; nothing has changed yet"
    )


async def test_async_load_returns_the_live_dict_so_in_place_edits_are_visible(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Mutating the loaded dict mutates storage - no defensive copying."""
    _seed_store(hass_storage, "e1", {"children": [{"id": "c1", "name": "A"}], "chores": []})
    storage = ChoremanderStorage(hass, "e1")

    loaded = await storage.async_load()
    loaded["children"][0]["name"] = "Z"

    child = storage.get_child("c1")
    assert child is not None and child.name == "Z", (
        "storage.data is the same object async_load returned, so an in-place "
        "rename must be visible through the getters"
    )


# ---------------------------------------------------------------------------
# The assigned_to name -> child id migration
# ---------------------------------------------------------------------------


async def test_migration_converts_child_names_to_ids_and_saves_exactly_once(
    hass: HomeAssistant, hass_storage: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Legacy `assigned_to` names become ids; already-valid ids pass through."""
    _seed_store(
        hass_storage,
        "legacy",
        {
            "children": [
                {"id": "c1", "name": "Krishna"},
                {"id": "c2", "name": "Radha"},
            ],
            "chores": [{"id": "ch1", "name": "Make Bed", "assigned_to": ["Krishna", "c2"]}],
        },
    )
    storage = ChoremanderStorage(hass, "legacy")
    saves = _track_saves(storage)

    with caplog.at_level(logging.INFO, logger=STORAGE_LOGGER):
        await storage.async_load()

    chore = storage.get_chore("ch1")
    assert chore is not None
    assert chore.assigned_to == ["c1", "c2"], (
        "'Krishna' must be rewritten to child id 'c1' while 'c2' - already a "
        f"valid id - is kept verbatim and in position; got {chore.assigned_to}"
    )
    assert len(saves) == 1, (
        "a migration that rewrote data must persist exactly once, not once per "
        f"chore and not zero times; saved {len(saves)} time(s)"
    )
    assert "Migrating chore 'Make Bed' assigned_to: 'Krishna' -> 'c1'" in caplog.text, (
        "each rewritten assignment must be announced so admins can audit it"
    )
    assert "Data migration completed" in caplog.text

    # The rewrite really reached disk: a fresh handle sees ids and re-migrates nothing.
    reloaded = ChoremanderStorage(hass, "legacy")
    resaves = _track_saves(reloaded)
    await reloaded.async_load()
    rechore = reloaded.get_chore("ch1")
    assert rechore is not None and rechore.assigned_to == ["c1", "c2"]
    assert resaves == [], "a migrated file must not be rewritten on every restart"


async def test_migration_does_not_save_when_all_assignments_are_already_ids(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Clean data must not cause a store rewrite on every HA start."""
    _seed_store(
        hass_storage,
        "clean",
        {
            "children": [{"id": "c1", "name": "Krishna"}],
            "chores": [{"id": "ch1", "name": "Make Bed", "assigned_to": ["c1"]}],
        },
    )
    storage = ChoremanderStorage(hass, "clean")
    saves = _track_saves(storage)

    await storage.async_load()

    assert saves == [], "nothing needed converting, so the migration must leave the file alone"
    chore = storage.get_chore("ch1")
    assert chore is not None and chore.assigned_to == ["c1"]


@pytest.mark.parametrize(
    ("case", "data"),
    [
        (
            "no children yet",
            {
                "children": [],
                "chores": [{"id": "ch1", "name": "X", "assigned_to": ["Krishna"]}],
            },
        ),
        (
            "no chores yet",
            {"children": [{"id": "c1", "name": "Krishna"}], "chores": []},
        ),
    ],
)
async def test_migration_short_circuits_when_children_or_chores_are_empty(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    case: str,
    data: dict[str, Any],
) -> None:
    """With nothing to map against, the migration returns before doing anything."""
    _seed_store(hass_storage, "short", data)
    storage = ChoremanderStorage(hass, "short")
    saves = _track_saves(storage)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        await storage.async_load()

    assert saves == [], f"{case}: the migration must not persist anything"
    assert _storage_log(caplog) == "", (
        f"{case}: with an empty side of the mapping there is nothing to warn about"
    )
    if data["chores"]:
        chore = storage.get_chore("ch1")
        assert chore is not None and chore.assigned_to == ["Krishna"], (
            "a name that cannot be resolved yet must survive untouched so a "
            "later load can still migrate it"
        )


async def test_migration_keeps_unknown_assignments_and_leaves_the_file_dirty(
    hass: HomeAssistant, hass_storage: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """An unresolvable assignment warns but never sets the `data_modified` flag."""
    _seed_store(
        hass_storage,
        "ghosts",
        {
            "children": [{"id": "c1", "name": "Krishna"}],
            "chores": [{"id": "ch1", "name": "Make Bed", "assigned_to": ["ghost", "c1"]}],
        },
    )
    storage = ChoremanderStorage(hass, "ghosts")
    saves = _track_saves(storage)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        await storage.async_load()

    chore = storage.get_chore("ch1")
    assert chore is not None and chore.assigned_to == ["ghost", "c1"], (
        "an unknown assignee is preserved verbatim rather than dropped - "
        "dropping it would silently unassign a chore"
    )
    assert "has unknown assigned_to value: 'ghost'" in caplog.text, (
        "a dangling assignee must be reported"
    )
    assert saves == [], (
        "only the name->id branch marks the data modified, so a file containing "
        "nothing but garbage references is never rewritten"
    )


async def test_migration_treats_a_value_as_an_id_before_treating_it_as_a_name(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """`valid_ids` is consulted first, so a correct id is never remapped."""
    _seed_store(
        hass_storage,
        "collide",
        {
            # 'Krishna' is simultaneously one child's ID and another child's NAME.
            "children": [
                {"id": "Krishna", "name": "Radha"},
                {"id": "c2", "name": "Krishna"},
            ],
            "chores": [{"id": "ch1", "name": "X", "assigned_to": ["Krishna"]}],
        },
    )
    storage = ChoremanderStorage(hass, "collide")
    saves = _track_saves(storage)

    await storage.async_load()

    chore = storage.get_chore("ch1")
    assert chore is not None and chore.assigned_to == ["Krishna"], (
        "'Krishna' is a valid child id here, so it must stay assigned to that "
        "child instead of being remapped to the child merely *named* Krishna"
    )
    assert saves == [], "nothing was converted, so nothing is persisted"


async def test_migration_collapses_duplicate_child_names_onto_the_last_child(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Two children sharing a name: the later one wins the name->id mapping."""
    _seed_store(
        hass_storage,
        "dupnames",
        {
            "children": [{"id": "c1", "name": "Sam"}, {"id": "c2", "name": "Sam"}],
            "chores": [{"id": "ch1", "name": "X", "assigned_to": ["Sam"]}],
        },
    )
    storage = ChoremanderStorage(hass, "dupnames")
    saves = _track_saves(storage)

    await storage.async_load()

    chore = storage.get_chore("ch1")
    assert chore is not None and chore.assigned_to == ["c2"], (
        "the name->id map is built by overwriting, so an ambiguous legacy name "
        "resolves to the LAST child with that name - the chore silently moves"
    )
    assert len(saves) == 1


async def test_migration_ignores_children_with_a_blank_id_when_building_the_map(
    hass: HomeAssistant, hass_storage: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """A child with a falsy id contributes neither a valid id nor a name mapping."""
    _seed_store(
        hass_storage,
        "blankid",
        {
            "children": [{"id": "", "name": "Ghost"}, {"id": "c1", "name": "Krishna"}],
            "chores": [{"id": "ch1", "name": "X", "assigned_to": ["Ghost"]}],
        },
    )
    storage = ChoremanderStorage(hass, "blankid")
    saves = _track_saves(storage)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        await storage.async_load()

    chore = storage.get_chore("ch1")
    assert chore is not None and chore.assigned_to == ["Ghost"], (
        "'Ghost' cannot be mapped because that child has no usable id, so the "
        "assignment is kept as-is"
    )
    assert "has unknown assigned_to value: 'Ghost'" in caplog.text
    assert saves == []


async def test_migration_never_adds_an_assigned_to_key_to_unassigned_chores(
    hass: HomeAssistant, hass_storage: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Chores with a missing or empty `assigned_to` are skipped entirely."""
    _seed_store(
        hass_storage,
        "unassigned",
        {
            "children": [{"id": "c1", "name": "Krishna"}],
            "chores": [{"id": "ch1", "name": "X"}, {"id": "ch2", "name": "Y", "assigned_to": []}],
        },
    )
    storage = ChoremanderStorage(hass, "unassigned")
    saves = _track_saves(storage)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        await storage.async_load()

    raw_chores = storage.data["chores"]
    assert "assigned_to" not in raw_chores[0], (
        "an everyone-chore stored without the key must not gain one - that is "
        "what keeps it assigned to every child"
    )
    assert raw_chores[1]["assigned_to"] == []
    assert saves == [], "nothing was assigned, so nothing was migrated or saved"
    assert _storage_log(caplog) == "", (
        "a chore with no assignees produces neither a migration nor a warning"
    )


async def test_dev_seed_needs_no_migration(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    dev_seed: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The shipped dev data is already id-based - loading it is read-only."""
    _seed_store(hass_storage, "dev_entry", dev_seed)
    storage = ChoremanderStorage(hass, "dev_entry")
    saves = _track_saves(storage)

    with caplog.at_level(logging.WARNING, logger=STORAGE_LOGGER):
        await storage.async_load()

    assert saves == [], "the dev seed must not be rewritten just by opening it"
    assert _storage_log(caplog) == "", (
        "every seeded assigned_to value is already a child id, so the migration "
        f"has nothing to say; got: {_storage_log(caplog)}"
    )


# ---------------------------------------------------------------------------
# The duplicate-append hazard: update_* upserts, add_* never deduplicates
# ---------------------------------------------------------------------------


async def test_update_child_chore_and_reward_upsert_when_the_id_is_unknown(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A typo'd id silently creates a second record instead of failing."""
    storage = ChoremanderStorage(hass, "upsert")
    await storage.async_load()
    storage.add_child(Child(name="Krishna", id="c1"))
    storage.add_chore(Chore(name="Make Bed", id="ch1"))
    storage.add_reward(Reward(name="Screen Time", id="r1"))

    storage.update_child(Child(name="Ghost", id="nope"))
    storage.update_chore(Chore(name="Ghost Chore", id="nope"))
    storage.update_reward(Reward(name="Ghost Reward", id="nope"))

    assert [c["id"] for c in storage.data["children"]] == ["c1", "nope"], (
        "update_child on an unknown id appends a brand-new child rather than "
        "raising - the caller has no way to tell an update from an insert"
    )
    assert [c["id"] for c in storage.data["chores"]] == ["ch1", "nope"]
    assert [r["id"] for r in storage.data["rewards"]] == ["r1", "nope"]


async def test_update_completion_and_reward_claim_are_silent_noops_when_absent(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Approval records deliberately do NOT upsert - a deleted one stays deleted."""
    storage = ChoremanderStorage(hass, "noop")
    await storage.async_load()

    storage.update_completion(
        ChoreCompletion(
            chore_id="ch1",
            child_id="c1",
            completed_at=datetime(2026, 8, 1, tzinfo=UTC),
            id="missing",
        )
    )
    storage.update_reward_claim(
        RewardClaim(
            reward_id="r1",
            child_id="c1",
            claimed_at=datetime(2026, 8, 1, tzinfo=UTC),
            id="missing",
        )
    )

    assert storage.data["completions"] == [], (
        "updating an unknown completion must not resurrect it as a new pending "
        "approval - unlike update_child, there is no add fallback here"
    )
    assert storage.data["reward_claims"] == [], (
        "updating an unknown reward claim must likewise stay a no-op"
    )


async def test_update_child_built_without_an_id_appends_a_duplicate(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Round-tripping a child through a fresh constructor loses its identity."""
    storage = ChoremanderStorage(hass, "identity")
    await storage.async_load()
    storage.add_child(Child(name="Krishna", id="c1"))

    # Rebuilding rather than mutating the object from get_child() means a new
    # auto-generated id, which no longer matches the stored record.
    storage.update_child(Child(name="Krishna", points=99))

    assert len(storage.data["children"]) == 2, (
        "the rebuilt child gets a fresh generated id, so update_child appends a "
        "second 'Krishna' instead of updating the first"
    )
    original = storage.get_child("c1")
    assert original is not None and original.points == 0, (
        "the original record is untouched by the accidental insert"
    )


async def test_update_child_with_a_blank_id_cannot_match_an_idless_record(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """`c.get("id")` is None for a legacy record, which never equals `""`."""
    _seed_store(hass_storage, "blank", {"children": [{"name": "Legacy"}], "chores": []})
    storage = ChoremanderStorage(hass, "blank")
    await storage.async_load()

    storage.update_child(Child(name="Legacy", id=""))

    assert len(storage.data["children"]) == 2, (
        "a stored child with no 'id' key reads back as None, which never matches "
        "the empty-string id, so the update lands as an append"
    )


async def test_add_child_accepts_a_duplicate_id_without_complaint(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """`add_*` has no uniqueness check at all."""
    storage = ChoremanderStorage(hass, "dupes")
    await storage.async_load()
    child = Child(name="Krishna", id="c1")

    storage.add_child(child)
    storage.add_child(child)

    assert len(storage.data["children"]) == 2, (
        "adding the same child twice yields two records - nothing enforces id "
        "uniqueness at the storage layer"
    )
    assert len(storage.get_children()) == 2


async def test_update_replaces_only_the_first_record_with_a_matching_id(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The early `return` means a duplicated id leaves a stale twin behind."""
    _seed_store(
        hass_storage,
        "twins",
        {
            "children": [
                {"id": "c1", "name": "A", "points": 1},
                {"id": "c1", "name": "B", "points": 2},
            ],
            "chores": [],
        },
    )
    storage = ChoremanderStorage(hass, "twins")
    await storage.async_load()

    storage.update_child(Child(name="Z", id="c1", points=99))

    children = storage.data["children"]
    assert len(children) == 2, "an update must not change the record count"
    assert children[0]["name"] == "Z" and children[0]["points"] == 99
    assert children[1]["name"] == "B" and children[1]["points"] == 2, (
        "only the first duplicate is rewritten; the second keeps its old values "
        "and will shadow nothing, because get_child also returns the first match"
    )
    first = storage.get_child("c1")
    assert first is not None and first.name == "Z", (
        "get_child returns the first match, so the stale twin stays invisible"
    )


@pytest.mark.parametrize(
    ("adder", "key", "item"),
    [
        ("add_child", "children", Child(name="Kid", id="k1")),
        ("add_chore", "chores", Chore(name="Chore", id="ch1")),
        ("add_reward", "rewards", Reward(name="Reward", id="r1")),
        (
            "add_completion",
            "completions",
            ChoreCompletion(
                chore_id="ch1",
                child_id="k1",
                completed_at=datetime(2026, 8, 1, tzinfo=UTC),
                id="cm1",
            ),
        ),
        (
            "add_reward_claim",
            "reward_claims",
            RewardClaim(
                reward_id="r1",
                child_id="k1",
                claimed_at=datetime(2026, 8, 1, tzinfo=UTC),
                id="rc1",
            ),
        ),
    ],
)
async def test_add_creates_its_collection_key_when_the_data_dict_is_bare(
    hass: HomeAssistant, adder: str, key: str, item: Any
) -> None:
    """Every `add_*` tolerates a data dict that predates its collection."""
    storage = ChoremanderStorage(hass, "bare")
    storage._data = {}

    getattr(storage, adder)(item)

    assert list(storage.data) == [key], (
        f"{adder} must create the '{key}' collection rather than raising KeyError"
    )
    assert len(storage.data[key]) == 1


# ---------------------------------------------------------------------------
# Serialization semantics
# ---------------------------------------------------------------------------


async def test_add_child_persists_every_one_of_the_ten_model_fields(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A field added to the dataclass but not to `to_dict` stops persisting."""
    storage = ChoremanderStorage(hass, "fields")
    await storage.async_load()

    storage.add_child(Child(name="Kid", id="k1"))

    assert storage.data["children"] == [
        {
            "name": "Kid",
            "avatar": "mdi:account-circle",
            "points": 0,
            "total_points_earned": 0,
            "total_chores_completed": 0,
            "current_streak": 0,
            "best_streak": 0,
            "pending_rewards": [],
            "chore_order": [],
            "id": "k1",
        }
    ], "a stored child must carry all ten documented fields verbatim"


async def test_add_chore_and_reward_persist_their_full_field_sets(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Chore rows have 16 keys, reward rows 9 - pinned so drift is visible."""
    storage = ChoremanderStorage(hass, "fields2")
    await storage.async_load()

    storage.add_chore(Chore(name="Make Bed", id="ch1"))
    storage.add_reward(Reward(name="Screen Time", id="r1"))

    assert set(storage.data["chores"][0]) == {
        "name",
        "points",
        "description",
        "due_days",
        "rotation",
        "rotation_order",
        "rotation_anchor",
        "day_overrides",
        "date_overrides",
        "assigned_to",
        "requires_approval",
        "time_category",
        "daily_limit",
        "completion_sound",
        "completion_percentage_per_month",
        "id",
    }, "stored chore field set changed"
    assert set(storage.data["rewards"][0]) == {
        "name",
        "cost",
        "description",
        "icon",
        "assigned_to",
        "is_jackpot",
        "override_point_value",
        "days_to_goal",
        "id",
    }, "stored reward field set changed"
    assert "is_dynamic" not in storage.data["rewards"][0], (
        "the legacy is_dynamic flag is migration-input only and must never be written back out"
    )


async def test_a_full_schedule_survives_a_save_and_a_fresh_load(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The schedule columns are the only nested structures a chore stores.

    Everything else on a chore is a scalar or a flat list, so this is the first
    time storage has had to carry a dict of lists. A reload here goes through
    the real HA store rather than `Chore.from_dict` alone, which is what proves
    the values reach disk in a shape that comes back.
    """
    storage = ChoremanderStorage(hass, "schedule_round_trip")
    await storage.async_load()

    storage.add_chore(
        Chore(
            name="Dishes",
            due_days=["saturday", "sunday"],
            rotation="occurrence",
            rotation_order=["child_krishna", "child_radha"],
            rotation_anchor="2026-08-08",
            day_overrides={"sunday": ["child_radha"], "monday": []},
            date_overrides={"2026-08-15": [], "2026-08-16": ["child_krishna"]},
            assigned_to=["child_krishna", "child_radha"],
            id="ch1",
        )
    )
    await storage.async_save()

    reloaded = ChoremanderStorage(hass, "schedule_round_trip")
    await reloaded.async_load()
    chore = reloaded.get_chore("ch1")

    assert chore is not None
    assert chore.due_days == ["saturday", "sunday"]
    assert chore.rotation == "occurrence"
    assert chore.rotation_order == ["child_krishna", "child_radha"]
    assert chore.rotation_anchor == "2026-08-08"
    assert chore.day_overrides == {"sunday": ["child_radha"], "monday": []}, (
        "an empty override list is a decision - 'nobody, this Monday' - and must not "
        "be dropped as though it were an empty slot"
    )
    assert chore.date_overrides == {"2026-08-15": [], "2026-08-16": ["child_krishna"]}


async def test_getters_return_fresh_objects_so_edits_need_an_explicit_update(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Deserializing per call is what makes `update_*` mandatory."""
    _seed_store(
        hass_storage,
        "fresh",
        {"children": [{"id": "c1", "name": "A", "points": 10}], "chores": []},
    )
    storage = ChoremanderStorage(hass, "fresh")
    await storage.async_load()

    first = storage.get_child("c1")
    second = storage.get_child("c1")
    assert first is not None and second is not None
    assert first is not second, (
        "every get_child call deserializes a new object; returning a cached one "
        "would let callers persist changes without calling update_child"
    )

    first.points = 999
    refetched = storage.get_child("c1")
    assert refetched is not None and refetched.points == 10, (
        "mutating a returned Child must be invisible to storage until update_child"
    )
    assert storage.data["children"][0]["points"] == 10

    storage.update_child(first)
    after = storage.get_child("c1")
    assert after is not None and after.points == 999, (
        "update_child is the only thing that commits the mutation"
    )


async def test_stored_rows_alias_the_model_list_objects(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """`to_dict()` does not copy lists - the stored row shares them."""
    storage = ChoremanderStorage(hass, "alias")
    await storage.async_load()
    chore = Chore(name="X", id="ch1", assigned_to=["c1"])
    storage.add_chore(chore)

    chore.assigned_to.append("c2")

    assert storage.data["chores"][0]["assigned_to"] == ["c1", "c2"], (
        "mutating the Chore's assigned_to list after add_chore reaches into "
        "stored state, because to_dict stores the same list object"
    )
    stored = storage.get_chore("ch1")
    assert stored is not None and stored.assigned_to == ["c1", "c2"], (
        "the aliasing is observable through the getters without any update_chore"
    )


# ---------------------------------------------------------------------------
# Pending filters
# ---------------------------------------------------------------------------


async def test_pending_getters_treat_a_missing_approved_flag_as_pending(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Legacy rows without `approved` must show up in the approvals queue."""
    _seed_store(
        hass_storage,
        "pending",
        {
            "children": [],
            "chores": [],
            "completions": [
                {
                    "id": "a",
                    "chore_id": "ch",
                    "child_id": "c",
                    "completed_at": "2026-08-01T10:00:00Z",
                    "approved": False,
                },
                {
                    "id": "b",
                    "chore_id": "ch",
                    "child_id": "c",
                    "completed_at": "2026-08-01T11:00:00Z",
                    "approved": True,
                },
                {
                    "id": "c_legacy",
                    "chore_id": "ch",
                    "child_id": "c",
                    "completed_at": "2026-08-01T12:00:00Z",
                },
            ],
            "reward_claims": [
                {
                    "id": "rc1",
                    "reward_id": "r",
                    "child_id": "c",
                    "claimed_at": "2026-08-01T10:00:00Z",
                },
                {
                    "id": "rc2",
                    "reward_id": "r",
                    "child_id": "c",
                    "claimed_at": "2026-08-01T11:00:00Z",
                    "approved": True,
                },
            ],
        },
    )
    storage = ChoremanderStorage(hass, "pending")
    await storage.async_load()

    assert len(storage.get_completions()) == 3
    assert [c.id for c in storage.get_pending_completions()] == ["a", "c_legacy"], (
        "an approved completion is filtered out, while a row with no 'approved' "
        "key defaults to False and therefore still awaits a parent"
    )
    assert [c.id for c in storage.get_pending_reward_claims()] == ["rc1"], (
        "reward claims filter identically on approved is False"
    )


# ---------------------------------------------------------------------------
# Removal semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("remover", "key"),
    [
        ("remove_child", "children"),
        ("remove_chore", "chores"),
        ("remove_reward", "rewards"),
        ("remove_completion", "completions"),
    ],
)
async def test_remove_creates_the_collection_key_even_when_it_did_not_exist(
    hass: HomeAssistant, remover: str, key: str
) -> None:
    """`remove_*` assigns unconditionally, unlike the guarded `add_*`."""
    storage = ChoremanderStorage(hass, "removals")
    storage._data = {}

    getattr(storage, remover)("anything")

    assert storage.data == {key: []}, (
        f"{remover} on a bare data dict materialises an empty '{key}' list rather than raising"
    )


async def test_removing_a_child_does_not_cascade_to_chores_or_completions(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Deleting a child leaves dangling references everywhere else."""
    _seed_store(
        hass_storage,
        "cascade",
        {
            "children": [{"id": "c1", "name": "A"}],
            "chores": [{"id": "ch1", "name": "X", "assigned_to": ["c1"]}],
            "completions": [
                {
                    "id": "cm1",
                    "chore_id": "ch1",
                    "child_id": "c1",
                    "completed_at": "2026-08-01T00:00:00Z",
                }
            ],
            "reward_claims": [
                {
                    "id": "rc1",
                    "reward_id": "r1",
                    "child_id": "c1",
                    "claimed_at": "2026-08-01T00:00:00Z",
                }
            ],
        },
    )
    storage = ChoremanderStorage(hass, "cascade")
    await storage.async_load()

    storage.remove_child("c1")

    assert storage.get_child("c1") is None, "the child itself is gone"
    assert storage.data["chores"][0]["assigned_to"] == ["c1"], (
        "the chore keeps pointing at the deleted child - downstream platforms "
        "have to tolerate the orphan"
    )
    assert len(storage.data["completions"]) == 1, "history is not cleaned up"
    assert len(storage.data["reward_claims"]) == 1, "pending claims are orphaned"


def test_remove_reward_claim_is_the_only_way_the_coordinator_deletes_a_claim() -> None:
    """Rejecting a claim goes through storage, not through its private dict.

    `async_reject_reward` used to filter `storage._data["reward_claims"]` in
    place. Every other collection has a remove_* method; this one now does too,
    and the coordinator must not reach past it.
    """
    assert hasattr(ChoremanderStorage, "remove_reward_claim")

    source = inspect.getsource(ChoremanderCoordinator.async_reject_reward)
    assert "storage.remove_reward_claim" in source
    assert "_data" not in source, "async_reject_reward must not touch storage._data directly"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


async def test_points_settings_fall_back_to_stars_only_when_the_key_is_absent(
    hass: HomeAssistant,
) -> None:
    """`.get(key, default)` returns a stored None, not the default."""
    storage = ChoremanderStorage(hass, "settings")
    storage._data = {}

    assert storage.get_points_name() == "Stars"
    assert storage.get_points_icon() == "mdi:star"

    storage.set_points_name(None)  # type: ignore[arg-type]
    assert storage.get_points_name() is None, (
        "an explicitly stored None is handed straight back - the 'Stars' default "
        "only covers a missing key, so a blank options-flow submit is sticky"
    )

    storage.set_points_name("")
    assert storage.get_points_name() == "", (
        "the setter performs no validation; an empty currency name is stored"
    )

    storage.set_points_icon("")
    assert storage.get_points_icon() == ""


# ---------------------------------------------------------------------------
# Store wiring and file-level round trips
# ---------------------------------------------------------------------------


async def test_each_config_entry_gets_its_own_namespaced_store_file(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Two entries must not share one file."""
    s1 = ChoremanderStorage(hass, "entry_a")
    s2 = ChoremanderStorage(hass, "entry_b")

    assert s1._store.key == "choremander.storage.entry_a"
    assert s2._store.key == "choremander.storage.entry_b"
    assert s1._store.version == STORAGE_VERSION == 2

    await s1.async_load()
    s1.add_child(Child(name="A", id="a"))
    await s1.async_save()
    await s2.async_load()

    assert [c.name for c in s1.get_children()] == ["A"]
    assert s2.get_children() == [], (
        "entry_b must not see entry_a's children; the store key is per entry_id"
    )
    assert hass_storage["choremander.storage.entry_a"]["data"]["children"][0]["id"] == "a"
    assert "choremander.storage.entry_b" not in hass_storage, (
        "loading defaults for a second entry writes no file"
    )


async def test_async_save_preserves_unrecognised_top_level_keys(
    hass: HomeAssistant, hass_storage: dict[str, Any], dev_seed: dict[str, Any]
) -> None:
    """The dev seed's vestigial `pending_*` collections survive a save cycle."""
    assert "pending_completions" in dev_seed and "pending_reward_claims" in dev_seed, (
        "this test is only meaningful while the seed still carries the legacy keys"
    )
    original_keys = set(dev_seed)
    _seed_store(hass_storage, "keeper", dev_seed)
    storage = ChoremanderStorage(hass, "keeper")
    await storage.async_load()

    storage.set_points_name("Coins")
    await storage.async_save()

    reloaded = ChoremanderStorage(hass, "keeper")
    data = await reloaded.async_load()

    assert set(data) == original_keys | {"points_transactions"}, (
        "storage writes self._data verbatim, so keys it never reads - "
        "pending_completions / pending_reward_claims - must not be dropped; "
        "the ledger key is the one thing load is allowed to add"
    )
    assert reloaded.get_points_name() == "Coins", "the edit was persisted"
    assert hass_storage["choremander.storage.keeper"]["version"] == STORAGE_VERSION
    assert hass_storage["choremander.storage.keeper"]["key"] == ("choremander.storage.keeper")


async def test_dev_seed_round_trips_losslessly_and_materialises_model_defaults(
    hass: HomeAssistant, hass_storage: dict[str, Any], dev_seed: dict[str, Any]
) -> None:
    """Load -> save -> load of the shipped seed changes nothing on disk."""
    pristine = copy.deepcopy(dev_seed)
    _seed_store(hass_storage, "seed", dev_seed)
    storage = ChoremanderStorage(hass, "seed")
    await storage.async_load()

    children = storage.get_children()
    chores = storage.get_chores()
    rewards = storage.get_rewards()
    assert [c.id for c in children] == ["child_krishna", "child_radha", "child_meera"]
    assert len(chores) == 11, "the dev seed defines eleven chores"
    assert len(rewards) == 5, "the dev seed defines five rewards"

    # Fields the seed omits are materialised from the dataclass defaults.
    bed = storage.get_chore("chore_bed")
    assert bed is not None
    assert bed.completion_percentage_per_month == 100, (
        "a seed chore with no completion_percentage_per_month is treated as a "
        "100%-of-days chore, which is what makes the pricing math whole numbers"
    )
    assert bed.completion_sound == "coin"
    assert bed.description == "" and bed.due_days == []
    assert bed.rotation == "none" and bed.rotation_order == [] and bed.rotation_anchor == "", (
        "a seed chore predates scheduling and must load with no rotation, which is "
        "what lets the schedule columns be additive with no storage version bump"
    )
    assert bed.day_overrides == {} and bed.date_overrides == {}

    game = storage.get_reward("reward_krishna_game")
    assert game is not None
    assert game.days_to_goal == 30, "a reward with no days_to_goal falls back to the 30-day default"
    assert game.override_point_value is True, "this reward uses its manual cost"
    screen = storage.get_reward("reward_screen")
    assert screen is not None
    assert screen.days_to_goal == 7 and screen.override_point_value is False

    krishna = storage.get_child("child_krishna")
    assert krishna is not None
    assert krishna.avatar == "mdi:account-circle"
    assert krishna.pending_rewards == [] and krishna.chore_order == []
    # The persisted streak fields are vestigial: the coordinator derives the
    # real figures from the completion history on every refresh and overwrites
    # whatever storage happened to be holding. Storage itself must not invent
    # a value.
    assert krishna.current_streak == 0 and krishna.best_streak == 0, (
        "storage returns the stored streak values verbatim; deriving them is "
        "the coordinator's job, covered in test_streaks.py"
    )

    loaded = dict(storage.data)
    await storage.async_save()
    reloaded = ChoremanderStorage(hass, "seed")
    data = await reloaded.async_load()

    assert data == loaded, (
        "deserializing and re-saving must be idempotent; storage only ever "
        "writes back the dict it read"
    )
    assert data == {**pristine, "points_transactions": []}, (
        "the only key load adds is the ledger, and only because a file written "
        "by hand or restored from a v1 backup may not carry it; every other "
        "key must survive untouched"
    )
