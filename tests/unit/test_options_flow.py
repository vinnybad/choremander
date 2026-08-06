"""Tests for `ChoremanderOptionsFlow` - the whole management UI.

Everything a parent can do outside of Lovelace (add/edit/delete children,
chores and rewards, rename the points currency) happens inside this one options
flow. It is built out of nested `async_show_menu` steps plus a `__getattr__`
hook that turns menu keys like `edit_reward_reward_zoo_trip` into a call to the
generic `async_step_edit_reward`.

Two things are worth knowing before reading the assertions:

* The flow never calls `async_create_entry`, so `entry.options` stays `{}`
  forever and every change is written straight through to `ChoremanderStorage`.
* Voluptuous validates `vol.Optional(..., default=X)` defaults on submit. Where
  a stored icon/avatar is not in the corresponding option list, the *form still
  renders* but any submit - including a plain "delete" - is rejected by the
  schema before the step ever runs. See `test_rewards_whose_icon_is_not_in_...`.
"""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander.config_flow import ChoremanderOptionsFlow
from custom_components.choremander.const import (
    AVATAR_OPTIONS,
    DOMAIN,
    REWARD_ICON_OPTIONS,
)
from custom_components.choremander.coordinator import ChoremanderCoordinator

# The dev seed is the closest thing this project has to a realistic data set:
# 3 children, 11 chores, 5 rewards. It comes from `tests/unit/dev_seed.py`, not
# from `dev/config/.storage/`, because the running dev container rewrites that
# file underneath the suite. The shared `dev_seed` fixture in
# tests/unit/conftest.py hands out a deep copy per test, because
# `to_dict()`/`from_dict()` alias the underlying lists.
SEED_ENTRY_ID = "seed_entry"
EMPTY_ENTRY_ID = "empty_entry"


# --------------------------------------------------------------------------- #
# Fixtures (local to this module - tests/unit/conftest.py is shared and frozen)
# --------------------------------------------------------------------------- #


@pytest.fixture
async def seeded_entry(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    dev_seed: dict[str, Any],
    bypass_frontend_registration: None,
) -> ConfigEntry:
    """A loaded config entry whose storage holds the dev seed."""
    hass_storage[f"choremander.storage.{SEED_ENTRY_ID}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"choremander.storage.{SEED_ENTRY_ID}",
        "data": dev_seed,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Choremander",
        data={"points_name": "Stars", "points_icon": "mdi:star"},
        entry_id=SEED_ENTRY_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture
def seeded_coordinator(hass: HomeAssistant, seeded_entry: ConfigEntry) -> ChoremanderCoordinator:
    """The live coordinator sitting on top of the dev seed."""
    return hass.data[DOMAIN][SEED_ENTRY_ID]


@pytest.fixture
async def empty_entry(hass: HomeAssistant, bypass_frontend_registration: None) -> ConfigEntry:
    """A loaded config entry with no children, chores or rewards."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Choremander",
        data={"points_name": "Stars", "points_icon": "mdi:star"},
        entry_id=EMPTY_ENTRY_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _open_options(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Open the options flow and return the main-menu result."""
    return await hass.config_entries.options.async_init(entry.entry_id)


async def _submit(hass: HomeAssistant, flow_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
    """Submit a form."""
    return await hass.config_entries.options.async_configure(flow_id, user_input)


async def _choose(hass: HomeAssistant, flow_id: str, step: str) -> dict[str, Any]:
    """Pick an entry from the currently displayed menu."""
    return await _submit(hass, flow_id, {"next_step_id": step})


async def _navigate(
    hass: HomeAssistant, entry: ConfigEntry, *steps: str
) -> tuple[str, dict[str, Any]]:
    """Open the options flow and walk down a chain of menu selections."""
    result = await _open_options(hass, entry)
    flow_id = result["flow_id"]
    for step in steps:
        result = await _choose(hass, flow_id, step)
    return flow_id, result


def _live_flow(hass: HomeAssistant, flow_id: str) -> ChoremanderOptionsFlow:
    """The in-progress options flow handler object itself."""
    return hass.config_entries.options._progress[flow_id]


# --------------------------------------------------------------------------- #
# Menus and routing
# --------------------------------------------------------------------------- #


async def test_the_main_menu_offers_children_chores_rewards_and_settings(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """The options entry point is a four-item menu, not a form."""
    result = await _open_options(hass, seeded_entry)

    assert result["type"] is data_entry_flow.FlowResultType.MENU, (
        "the options flow opens on a menu; a form here would mean the step ids have been reshuffled"
    )
    assert result["step_id"] == "init"
    assert result["menu_options"] == {
        "manage_children": "Manage Children",
        "manage_chores": "Manage Chores",
        "manage_rewards": "Manage Rewards",
        "settings": "Settings",
    }, "the top-level menu labels are the primary navigation the parent sees"


async def test_manage_children_menu_lists_each_child_between_add_and_back(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """Children appear in storage order, sandwiched by Add and Back."""
    _flow_id, result = await _navigate(hass, seeded_entry, "manage_children")

    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert list(result["menu_options"]) == [
        "add_child",
        "edit_child_child_krishna",
        "edit_child_child_radha",
        "edit_child_child_meera",
        "init",
    ], "menu order drives the on-screen order: Add first, children, Back last"
    assert result["menu_options"]["edit_child_child_krishna"] == "Edit: Krishna"


async def test_selecting_a_child_routes_to_the_generic_edit_child_form(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """`edit_child_<id>` is synthesised by `__getattr__` and stashes the id."""
    flow_id, result = await _navigate(
        hass, seeded_entry, "manage_children", "edit_child_child_krishna"
    )

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "edit_child", (
        "every child edits through the one generic step; the per-child menu key "
        "only exists to select a target"
    )
    assert result["description_placeholders"] == {"child_name": "Krishna"}
    assert _live_flow(hass, flow_id)._selected_child_id == "child_krishna", (
        "__getattr__ must record which child was picked, otherwise the form "
        "would edit whichever child was last visited"
    )


async def test_getattr_routes_three_edit_prefixes_and_rejects_anything_else(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """The dynamic-step hook handles child/chore/reward and nothing else."""
    flow = ChoremanderOptionsFlow(seeded_entry)

    assert flow.async_step_edit_child_abc123 == flow.async_step_edit_child, (
        "edit_child_<id> must resolve to the generic child step"
    )
    assert flow._selected_child_id == "abc123"

    assert flow.async_step_edit_chore_chore_bed == flow.async_step_edit_chore
    assert flow._selected_chore_id == "chore_bed"

    assert flow.async_step_edit_reward_reward_zoo_trip == flow.async_step_edit_reward
    assert flow._selected_reward_id == "reward_zoo_trip"

    with pytest.raises(AttributeError, match="async_step_bogus"):
        getattr(flow, "async_step_bogus")  # noqa: B009 - the lookup itself is the assertion
    assert "async_step_edit_child_abc123" not in ChoremanderOptionsFlow.__dict__, (
        "the routing is purely dynamic - no real methods are being created"
    )


async def test_an_unknown_menu_target_is_rejected_and_the_flow_stays_alive(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """A `next_step_id` outside the menu never reaches `__getattr__`."""
    result = await _open_options(hass, seeded_entry)
    flow_id = result["flow_id"]

    with pytest.raises(data_entry_flow.InvalidData) as err:
        await _choose(hass, flow_id, "edit_child_does_not_exist")
    assert "data['next_step_id']" in str(err.value), (
        "the menu schema, not the step router, is what rejects unknown targets"
    )

    recovered = await _choose(hass, flow_id, "settings")
    assert recovered["step_id"] == "settings", (
        "a rejected submission must leave the flow usable rather than aborting it"
    )


async def test_chore_menu_labels_append_the_time_category_except_for_anytime(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """`[morning]` is shown for scheduled chores and omitted for 'anytime'."""
    await seeded_coordinator.async_add_chore(name="Anything", points=7)

    _flow_id, result = await _navigate(hass, seeded_entry, "manage_chores")
    labels = list(result["menu_options"].values())

    assert "Edit: Make Bed (2 pts) [morning]" in labels, (
        "a chore with a time category shows it in brackets"
    )
    assert "Edit: Anything (7 pts)" in labels, (
        "'anytime' is the default and is deliberately not spelled out in the label"
    )


async def test_reward_menu_labels_show_the_static_cost_not_the_dynamic_price(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """The options menu advertises `reward.cost`, which nobody is ever charged.

    30 Min Screen Time uses dynamic pricing, so the real price is 108 for
    Krishna, 91 for Radha and 23 for Meera - but the management menu says
    "10 pts".
    """
    reward = seeded_coordinator.get_reward("reward_screen")
    assert seeded_coordinator.calculate_dynamic_reward_costs(reward) == {
        "child_krishna": 108,
        "child_radha": 91,
        "child_meera": 23,
    }, "sanity check on the dev seed: this reward really is dynamically priced"

    _flow_id, result = await _navigate(hass, seeded_entry, "manage_rewards")

    assert (
        result["menu_options"]["edit_reward_reward_screen"] == "Edit: 30 Min Screen Time (10 pts)"
    ), (
        "the label is built from the static cost field, so a parent editing a "
        "dynamic reward is shown a number no child will ever pay"
    )


# --------------------------------------------------------------------------- #
# Adding chores
# --------------------------------------------------------------------------- #


async def test_add_chore_defaults_are_applied_and_numbers_arrive_as_ints(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Submitting only a name fills in every other field from the schema."""
    recorded: list[dict[str, Any]] = []
    real_add_chore = seeded_coordinator.async_add_chore

    async def spy(**kwargs: Any):
        recorded.append(kwargs)
        return await real_add_chore(**kwargs)

    seeded_coordinator.async_add_chore = spy

    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "add_chore")
    result = await _submit(hass, flow_id, {"name": "Sweep"})

    assert result["step_id"] == "manage_chores", (
        "a successful add drops the parent back on the chores menu"
    )
    assert recorded == [
        {
            "name": "Sweep",
            "points": 10,
            "description": "",
            "due_days": [],
            "assigned_to": [],
            "requires_approval": True,
            "time_category": "anytime",
            "daily_limit": 1,
            "completion_percentage_per_month": 100,
            "completion_sound": "coin",
        }
    ], "the defaults baked into the add-chore schema define what a bare add creates"

    numeric = recorded[0]
    for key in ("points", "daily_limit", "completion_percentage_per_month"):
        assert isinstance(numeric[key], int), (
            f"{key} must reach the coordinator as an int - NumberSelector hands back "
            "a float and the flow is responsible for the int() cast"
        )


async def test_add_chore_with_a_whitespace_only_name_reshows_the_form_with_name_required(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """A name of spaces is caught by the step, not the schema."""
    chores_before = len(seeded_coordinator.storage.get_chores())

    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "add_chore")
    result = await _submit(hass, flow_id, {"name": "   "})

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "add_chore", "the form is redisplayed for correction"
    assert result["errors"] == {"name": "name_required"}, (
        "a whitespace-only name must surface as a field error the parent can fix"
    )
    assert len(seeded_coordinator.storage.get_chores()) == chores_before, (
        "no chore may be created from a blank name"
    )


async def test_add_chore_without_a_name_key_is_rejected_by_the_schema(
    hass: HomeAssistant, seeded_entry: ConfigEntry
) -> None:
    """`name` is Required with no default, so omitting it is a hard failure."""
    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "add_chore")

    with pytest.raises(data_entry_flow.InvalidData) as err:
        await _submit(hass, flow_id, {})
    assert "data['name']" in str(err.value), (
        "an omitted name cannot be turned into a friendly form error - voluptuous "
        "rejects it before async_step_add_chore is entered"
    )


async def test_assigned_to_is_omitted_from_the_add_chore_schema_when_there_are_no_children(
    hass: HomeAssistant, empty_entry: ConfigEntry
) -> None:
    """With nobody to assign to, the field disappears and becomes illegal."""
    flow_id, menu = await _navigate(hass, empty_entry, "manage_chores")
    assert list(menu["menu_options"]) == ["add_chore", "add_chores_bulk", "init"], (
        "an empty install offers only the two add actions and Back"
    )

    result = await _choose(hass, flow_id, "add_chore")
    assert "assigned_to" not in result["data_schema"].schema, (
        "the assigned_to selector is only added when child_options is non-empty"
    )

    with pytest.raises(data_entry_flow.InvalidData) as err:
        await _submit(hass, flow_id, {"name": "Sweep", "assigned_to": ["child_ghost"]})
    assert "data['assigned_to']" in str(err.value), (
        "voluptuous defaults to PREVENT_EXTRA, so sending the absent key fails"
    )


async def test_bulk_add_splits_names_on_commas_and_newlines_and_trims_them(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """One textarea becomes several chores; blank lines are dropped."""
    before = {chore.id for chore in seeded_coordinator.storage.get_chores()}

    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "add_chores_bulk")
    result = await _submit(hass, flow_id, {"chore_names": "Dishes, Laundry\nVacuum\n\n  Trash  "})

    assert result["step_id"] == "manage_chores"
    created = [
        chore.name for chore in seeded_coordinator.storage.get_chores() if chore.id not in before
    ]
    assert created == ["Dishes", "Laundry", "Vacuum", "Trash"], (
        "commas and newlines are equivalent separators, surrounding whitespace is "
        "stripped, and empty segments produce no chore"
    )


async def test_bulk_add_reports_name_required_for_blank_and_for_separator_only_input(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Two different code paths produce the same error for the parent."""
    chores_before = len(seeded_coordinator.storage.get_chores())
    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "add_chores_bulk")

    blank = await _submit(hass, flow_id, {"chore_names": "   "})
    assert blank["errors"] == {"chore_names": "name_required"}, (
        "an all-whitespace textarea is rejected by the early `if not raw` guard"
    )

    separators_only = await _submit(hass, flow_id, {"chore_names": ",,,\n\n"})
    assert separators_only["errors"] == {"chore_names": "name_required"}, (
        "input that parses to zero names is rejected by the second guard, after "
        "splitting - a distinct branch that reports the same error"
    )

    assert len(seeded_coordinator.storage.get_chores()) == chores_before, (
        "neither rejected submission may create a chore"
    )


# --------------------------------------------------------------------------- #
# Editing and deleting
# --------------------------------------------------------------------------- #


async def test_saving_an_edited_chore_writes_every_field_back_to_storage(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """A full edit round-trips all ten editable fields and keeps the id."""
    flow_id, _result = await _navigate(
        hass, seeded_entry, "manage_chores", "edit_chore_chore_teeth_am"
    )
    result = await _submit(
        hass,
        flow_id,
        {
            "name": "Brush Teeth",
            "description": "Two minutes",
            "points": 4.0,
            "time_category": "night",
            "due_days": ["monday", "friday"],
            "requires_approval": True,
            "daily_limit": 3.0,
            "completion_percentage_per_month": 50.0,
            "completion_sound": "fanfare",
            "assigned_to": ["child_radha"],
            "action": "save",
        },
    )

    assert result["step_id"] == "manage_chores"
    stored = next(
        raw for raw in seeded_coordinator.storage._data["chores"] if raw["id"] == "chore_teeth_am"
    )
    assert stored == {
        "name": "Brush Teeth",
        "points": 4,
        "description": "Two minutes",
        "due_days": ["monday", "friday"],
        "rotation": "none",
        "rotation_order": [],
        "rotation_anchor": "",
        "day_overrides": {},
        "date_overrides": {},
        "assigned_to": ["child_radha"],
        "requires_approval": True,
        "time_category": "night",
        "daily_limit": 3,
        "completion_sound": "fanfare",
        "completion_percentage_per_month": 50,
        "id": "chore_teeth_am",
    }, (
        "every field on the edit form must be persisted, and the id must not change. "
        "The rotation and override columns have no form field on purpose - the schedule "
        "card is their editing surface - so they appear here at their defaults"
    )
    assert isinstance(stored["points"], int) and isinstance(stored["daily_limit"], int), (
        "NumberSelector returns floats; storing 4.0 instead of 4 would leak into "
        "every points calculation downstream"
    )


async def test_saving_an_unchanged_seed_chore_materialises_the_fields_it_never_had(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Opening and saving a legacy chore fills in the defaulted columns.

    The seeded `chore_bed` row has no `description`, `due_days`,
    `completion_sound` or `completion_percentage_per_month`. A no-op save
    rewrites the row from the model, so those keys appear for the first time.
    """
    original = next(
        raw for raw in seeded_coordinator.storage._data["chores"] if raw["id"] == "chore_bed"
    )
    assert "completion_sound" not in original, (
        "precondition: the dev seed predates the sound and percentage fields"
    )

    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "edit_chore_chore_bed")
    await _submit(hass, flow_id, {"action": "save"})

    stored = next(
        raw for raw in seeded_coordinator.storage._data["chores"] if raw["id"] == "chore_bed"
    )
    assert stored == {
        "name": "Make Bed",
        "points": 2,
        "description": "",
        "due_days": [],
        "rotation": "none",
        "rotation_order": [],
        "rotation_anchor": "",
        "day_overrides": {},
        "date_overrides": {},
        "assigned_to": ["child_krishna", "child_radha"],
        "requires_approval": False,
        "time_category": "morning",
        "daily_limit": 1,
        "completion_sound": "coin",
        "completion_percentage_per_month": 100,
        "id": "chore_bed",
    }, (
        "a save with no user edits still rewrites the row through Chore.to_dict(), "
        "so the model defaults become persisted values"
    )


async def test_saving_the_chore_form_leaves_a_schedule_set_on_the_card_alone(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Editing points must not silently delete a rotation.

    The rotation and override columns have no field on this form - the schedule
    card owns them - and the save path assigns only the fields the form
    submitted. If it ever rebuilt the chore from `user_input` instead, a parent
    nudging a chore from 2 points to 3 would wipe a fortnight of scheduling with
    nothing to warn them.
    """
    chore = seeded_coordinator.get_chore("chore_teeth_am")
    chore.rotation = "week"
    chore.rotation_order = ["child_krishna", "child_radha"]
    chore.rotation_anchor = "2026-08-03"
    chore.day_overrides = {"sunday": []}
    chore.date_overrides = {"2026-08-08": ["child_radha"]}
    await seeded_coordinator.async_update_chore(chore)

    flow_id, _result = await _navigate(
        hass, seeded_entry, "manage_chores", "edit_chore_chore_teeth_am"
    )
    await _submit(hass, flow_id, {"points": 3.0, "action": "save"})

    saved = seeded_coordinator.get_chore("chore_teeth_am")
    assert saved is not None
    assert saved.points == 3, "precondition: the edit the parent actually made went through"
    assert saved.rotation == "week"
    assert saved.rotation_order == ["child_krishna", "child_radha"]
    assert saved.rotation_anchor == "2026-08-03"
    assert saved.day_overrides == {"sunday": []}
    assert saved.date_overrides == {"2026-08-08": ["child_radha"]}


async def test_delete_removes_the_child_chore_or_reward_from_its_menu(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """`{"action": "delete"}` is enough - the other fields default in.

    Meera is the child deleted here because no chore this test goes on to edit
    is assigned to her. Deleting a child does not scrub their id out of
    `chore.assigned_to`, and the edit-chore form validates the stored value
    against the children who still exist, so editing Make Bed after deleting
    Krishna or Radha fails on a chore nobody touched.
    """
    flow_id, _result = await _navigate(
        hass, seeded_entry, "manage_children", "edit_child_child_meera"
    )
    result = await _submit(hass, flow_id, {"action": "delete"})
    assert "edit_child_child_meera" not in result["menu_options"], (
        "a deleted child disappears from the children menu immediately"
    )
    assert seeded_coordinator.get_child("child_meera") is None

    await _choose(hass, flow_id, "init")
    await _choose(hass, flow_id, "manage_chores")
    await _choose(hass, flow_id, "edit_chore_chore_bed")
    result = await _submit(hass, flow_id, {"action": "delete"})
    assert "edit_chore_chore_bed" not in result["menu_options"]
    assert seeded_coordinator.get_chore("chore_bed") is None

    await _choose(hass, flow_id, "init")
    await _choose(hass, flow_id, "manage_rewards")
    await _choose(hass, flow_id, "edit_reward_reward_screen")
    result = await _submit(hass, flow_id, {"action": "delete"})
    assert "edit_reward_reward_screen" not in result["menu_options"]
    assert seeded_coordinator.get_reward("reward_screen") is None


@pytest.mark.parametrize(
    ("reward_id", "icon"),
    [
        ("reward_radha_book", "mdi:book-open-variant"),
        ("reward_movie_night", "mdi:movie-open"),
        ("reward_zoo_trip", "mdi:elephant"),
    ],
)
async def test_rewards_whose_icon_is_not_in_reward_icon_options_cannot_be_deleted(
    hass: HomeAssistant,
    seeded_coordinator: ChoremanderCoordinator,
    seeded_entry: ConfigEntry,
    reward_id: str,
    icon: str,
) -> None:
    """A reward with an off-list icon is un-editable AND un-deletable.

    CURRENT BEHAVIOUR (defect #6, see test plan section 6.2) - the edit form
    seeds `vol.Optional("icon", default=reward.icon)` with a value that is not
    among `REWARD_ICON_OPTIONS`. Voluptuous validates defaults on submit, so
    even a bare "delete" is rejected before the step runs, and the parent has no
    way to remove the reward from the UI at all. Three of the five dev-seed
    rewards are stuck like this. This test pins today's behaviour so a future
    fix shows up as a deliberate change.
    """
    assert icon not in REWARD_ICON_OPTIONS, (
        f"precondition: {icon} is genuinely missing from the reward icon list"
    )

    flow_id, form = await _navigate(
        hass, seeded_entry, "manage_rewards", f"edit_reward_{reward_id}"
    )
    assert form["type"] is data_entry_flow.FlowResultType.FORM, (
        "the form still renders - nothing warns the parent before they submit"
    )

    with pytest.raises(data_entry_flow.InvalidData) as err:
        await _submit(hass, flow_id, {"action": "delete"})
    assert "data['icon']" in str(err.value), (
        "the stored icon itself is what fails validation, so neither Save nor "
        "Delete can be used on this reward"
    )
    assert seeded_coordinator.get_reward(reward_id) is not None, (
        "the reward survives the failed delete"
    )

    # The escape hatch: explicitly picking a listed icon lets the submit through.
    result = await _submit(hass, flow_id, {"action": "delete", "icon": REWARD_ICON_OPTIONS[0]})
    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert seeded_coordinator.get_reward(reward_id) is None, (
        "overriding the icon with a valid one is the only route to deletion"
    )


async def test_a_child_whose_avatar_is_not_in_avatar_options_cannot_be_deleted(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """The same defaults-are-validated trap applies to child avatars.

    CURRENT BEHAVIOUR (defect #6, see test plan section 6.2) - `async_add_child`
    accepts any avatar string, but `async_step_edit_child` can only round-trip
    the values in `AVATAR_OPTIONS`.
    """
    assert "mdi:elephant" not in AVATAR_OPTIONS, "precondition for this trap"
    child = await seeded_coordinator.async_add_child(name="Ghost", avatar="mdi:elephant")

    flow_id, form = await _navigate(hass, seeded_entry, "manage_children", f"edit_child_{child.id}")
    assert form["type"] is data_entry_flow.FlowResultType.FORM

    with pytest.raises(data_entry_flow.InvalidData) as err:
        await _submit(hass, flow_id, {"name": "Ghost", "action": "delete"})
    assert "data['avatar']" in str(err.value), (
        "an avatar the coordinator happily stored cannot be validated back in, so "
        "the child can be neither renamed nor removed"
    )
    assert seeded_coordinator.get_child(child.id) is not None


async def test_editing_a_child_deleted_mid_flow_falls_back_to_the_children_menu(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """A vanished target degrades to the parent menu rather than crashing."""
    flow_id, _result = await _navigate(
        hass, seeded_entry, "manage_children", "edit_child_child_radha"
    )

    await seeded_coordinator.async_remove_child("child_radha")

    result = await _submit(hass, flow_id, {"name": "Radha", "action": "save"})
    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "manage_children", (
        "the step re-reads the child up front; when it is gone the flow bounces "
        "back to the menu instead of raising"
    )
    assert "edit_child_child_radha" not in result["menu_options"]


async def test_editing_a_reward_deleted_mid_flow_falls_back_to_the_rewards_menu(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """The reward step has the same vanished-target guard as the child step."""
    flow_id, _result = await _navigate(
        hass, seeded_entry, "manage_rewards", "edit_reward_reward_screen"
    )

    await seeded_coordinator.async_remove_reward("reward_screen")

    result = await _submit(
        hass,
        flow_id,
        {"name": "30 Min Screen Time", "days_to_goal": 7, "action": "save"},
    )
    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "manage_rewards"
    assert "edit_reward_reward_screen" not in result["menu_options"]


async def test_editing_a_chore_deleted_mid_flow_falls_back_to_the_chores_menu(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """And so does the chore step - all three targets are re-read up front."""
    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "edit_chore_chore_bed")

    await seeded_coordinator.async_remove_chore("chore_bed")

    result = await _submit(hass, flow_id, {"name": "Make Bed", "action": "save"})
    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "manage_chores"
    assert "edit_chore_chore_bed" not in result["menu_options"]


# --------------------------------------------------------------------------- #
# Adding and editing children and rewards
# --------------------------------------------------------------------------- #


async def test_adding_a_child_persists_it_and_returns_to_the_children_menu(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """`add_child` trims the name, defaults the avatar and lands back on the menu."""
    flow_id, form = await _navigate(hass, seeded_entry, "manage_children", "add_child")
    assert form["step_id"] == "add_child"

    result = await _submit(hass, flow_id, {"name": "  Gopi  "})

    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "manage_children"
    added = [c for c in seeded_coordinator.storage.get_children() if c.name == "Gopi"]
    assert len(added) == 1, "exactly one child should have been created"
    child = added[0]
    assert child.avatar == "mdi:account-circle", "the schema default is applied"
    assert child.points == 0 and child.total_points_earned == 0
    assert f"edit_child_{child.id}" in result["menu_options"], (
        "the new child must be selectable from the menu the flow returns to"
    )


async def test_adding_a_child_with_a_whitespace_only_name_reshows_the_form(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """A blank name is caught by the step, not by the schema."""
    before = len(seeded_coordinator.storage.get_children())
    flow_id, _form = await _navigate(hass, seeded_entry, "manage_children", "add_child")

    result = await _submit(hass, flow_id, {"name": "   "})

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "add_child"
    assert result["errors"] == {"name": "name_required"}
    assert len(seeded_coordinator.storage.get_children()) == before, "nothing was created"


async def test_saving_an_edited_child_writes_the_name_and_avatar_back(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """`{"action": "save"}` rewrites only the two editable fields."""
    flow_id, _form = await _navigate(
        hass, seeded_entry, "manage_children", "edit_child_child_krishna"
    )

    result = await _submit(
        hass,
        flow_id,
        {"name": "Krishna B", "avatar": AVATAR_OPTIONS[1], "action": "save"},
    )

    assert result["step_id"] == "manage_children"
    child = seeded_coordinator.get_child("child_krishna")
    assert child is not None
    assert (child.name, child.avatar) == ("Krishna B", AVATAR_OPTIONS[1])
    assert child.points == 25, "editing a child must not touch its balance"
    assert result["menu_options"]["edit_child_child_krishna"] == "Edit: Krishna B"


async def test_adding_a_reward_coerces_the_numeric_fields_to_ints(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """The NumberSelectors hand back floats; `add_reward` must store ints."""
    flow_id, form = await _navigate(hass, seeded_entry, "manage_rewards", "add_reward")
    assert form["step_id"] == "add_reward"

    result = await _submit(
        hass,
        flow_id,
        {
            "name": "Ice Cream",
            "description": "a scoop",
            "icon": REWARD_ICON_OPTIONS[0],
            "cost": 12,
            "days_to_goal": 5,
            "is_jackpot": True,
            "override_point_value": True,
            "assigned_to": ["child_radha"],
        },
    )

    assert result["step_id"] == "manage_rewards"
    added = [r for r in seeded_coordinator.storage.get_rewards() if r.name == "Ice Cream"]
    assert len(added) == 1
    reward = added[0]
    assert isinstance(reward.cost, int) and reward.cost == 12
    assert isinstance(reward.days_to_goal, int) and reward.days_to_goal == 5
    assert reward.is_jackpot is True and reward.override_point_value is True
    assert reward.assigned_to == ["child_radha"]
    assert reward.description == "a scoop" and reward.icon == REWARD_ICON_OPTIONS[0]


async def test_adding_a_reward_with_a_blank_name_reshows_the_form(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Same blank-name guard as `add_child`, on the reward side."""
    before = len(seeded_coordinator.storage.get_rewards())
    flow_id, _form = await _navigate(hass, seeded_entry, "manage_rewards", "add_reward")

    result = await _submit(hass, flow_id, {"name": " ", "days_to_goal": 30})

    assert result["type"] is data_entry_flow.FlowResultType.FORM
    assert result["step_id"] == "add_reward"
    assert result["errors"] == {"name": "name_required"}
    assert len(seeded_coordinator.storage.get_rewards()) == before


async def test_saving_an_edited_reward_writes_every_field_back_to_storage(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """A `save` on `edit_reward` rewrites the whole row, ints included."""
    flow_id, _form = await _navigate(
        hass, seeded_entry, "manage_rewards", "edit_reward_reward_screen"
    )

    result = await _submit(
        hass,
        flow_id,
        {
            "name": "60 Min Screen Time",
            "description": "double time",
            "icon": REWARD_ICON_OPTIONS[1],
            "cost": 20,
            "days_to_goal": 14,
            "is_jackpot": True,
            "override_point_value": True,
            "assigned_to": ["child_krishna"],
            "action": "save",
        },
    )

    assert result["step_id"] == "manage_rewards"
    reward = seeded_coordinator.get_reward("reward_screen")
    assert reward is not None
    assert reward.name == "60 Min Screen Time"
    assert reward.description == "double time"
    assert reward.icon == REWARD_ICON_OPTIONS[1]
    assert isinstance(reward.cost, int) and reward.cost == 20
    assert isinstance(reward.days_to_goal, int) and reward.days_to_goal == 14
    assert reward.is_jackpot is True and reward.override_point_value is True
    assert reward.assigned_to == ["child_krishna"]
    assert result["menu_options"]["edit_reward_reward_screen"] == (
        "Edit: 60 Min Screen Time (20 pts)"
    )


# --------------------------------------------------------------------------- #
# Settings, validation bounds, and entry options
# --------------------------------------------------------------------------- #


async def test_settings_prefills_from_storage_persists_there_and_returns_to_the_main_menu(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Renaming the currency writes to storage - never to `entry.data/options`."""
    flow_id, form = await _navigate(hass, seeded_entry, "settings")

    defaults = {str(key): key.default() for key in form["data_schema"].schema if key.default}
    assert defaults == {"points_name": "Stars", "points_icon": "mdi:star"}, (
        "the settings form must prefill from current storage, not from constants"
    )

    result = await _submit(hass, flow_id, {"points_name": "Coins", "points_icon": "mdi:cash"})
    assert result["type"] is data_entry_flow.FlowResultType.MENU
    assert result["step_id"] == "init", "saving settings returns to the main menu"

    assert seeded_coordinator.storage.get_points_name() == "Coins"
    assert seeded_coordinator.storage.get_points_icon() == "mdi:cash"
    assert seeded_entry.data == {"points_name": "Stars", "points_icon": "mdi:star"}, (
        "entry.data is left stale - which is why async_setup_entry reverts the "
        "rename on the next restart (defect #7, see test plan section 6.2)"
    )
    assert seeded_entry.options == {}


@pytest.mark.parametrize(
    ("step", "user_input", "field"),
    [
        ("add_chore", {"name": "X", "points": 0}, "points"),
        ("add_chore", {"name": "X", "points": 1001}, "points"),
        ("add_chore", {"name": "X", "daily_limit": 11}, "daily_limit"),
    ],
)
async def test_out_of_range_chore_numbers_are_rejected_by_the_schema(
    hass: HomeAssistant,
    seeded_entry: ConfigEntry,
    step: str,
    user_input: dict[str, Any],
    field: str,
) -> None:
    """Selector bounds raise `InvalidData` instead of producing form errors."""
    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", step)

    with pytest.raises(data_entry_flow.InvalidData) as err:
        await _submit(hass, flow_id, user_input)
    assert f"data['{field}']" in str(err.value), (
        f"{field} is bounded by its NumberSelector, so an out-of-range value never "
        "reaches the step and cannot be reported as a friendly error"
    )


async def test_a_completion_percentage_of_zero_is_accepted_because_the_minimum_is_zero(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """0% is legal even though it disables dynamic pricing for that chore."""
    flow_id, _result = await _navigate(hass, seeded_entry, "manage_chores", "add_chore")

    result = await _submit(hass, flow_id, {"name": "Never", "completion_percentage_per_month": 0})

    assert result["step_id"] == "manage_chores", (
        "completion_percentage_per_month has min=0, unlike points and daily_limit"
    )
    created = next(
        chore for chore in seeded_coordinator.storage.get_chores() if chore.name == "Never"
    )
    assert created.completion_percentage_per_month == 0


@pytest.mark.parametrize("days_to_goal", [0, 366])
async def test_days_to_goal_outside_one_to_three_sixty_five_is_rejected(
    hass: HomeAssistant, seeded_entry: ConfigEntry, days_to_goal: int
) -> None:
    """The reward goal horizon is bounded by its NumberSelector."""
    flow_id, _result = await _navigate(hass, seeded_entry, "manage_rewards", "add_reward")

    with pytest.raises(data_entry_flow.InvalidData) as err:
        await _submit(hass, flow_id, {"name": "X", "days_to_goal": days_to_goal})
    assert "data['days_to_goal']" in str(err.value)


async def test_the_options_flow_never_creates_an_entry_or_writes_entry_options(
    hass: HomeAssistant, seeded_coordinator: ChoremanderCoordinator, seeded_entry: ConfigEntry
) -> None:
    """Every step ends on a form or a menu; all state goes through storage."""
    flow_id, result = await _navigate(hass, seeded_entry)
    seen = [result]

    seen.append(await _choose(hass, flow_id, "manage_children"))
    seen.append(await _choose(hass, flow_id, "add_child"))
    seen.append(await _submit(hass, flow_id, {"name": "Nikhil"}))
    seen.append(await _choose(hass, flow_id, "init"))
    seen.append(await _choose(hass, flow_id, "settings"))
    seen.append(await _submit(hass, flow_id, {"points_name": "Coins", "points_icon": "mdi:cash"}))

    assert all(
        r["type"] in (data_entry_flow.FlowResultType.FORM, data_entry_flow.FlowResultType.MENU)
        for r in seen
    ), (
        "async_create_entry is never called, so the flow stays open until the user "
        "closes it: "
        f"{[r['type'] for r in seen]}"
    )
    assert seeded_entry.options == {}, (
        "Choremander stores everything in its own Store; entry.options must remain "
        "empty or a restart would start honouring half-written option data"
    )
    assert any(child.name == "Nikhil" for child in seeded_coordinator.storage.get_children()), (
        "the changes made along the way really were persisted, just not into options"
    )
