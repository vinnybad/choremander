"""CRUD behaviour of `ChoremanderCoordinator`.

Covers add/update/remove for children, chores and rewards, the bulk-chore
helper, per-child chore ordering, the shape of the coordinator's refreshed
data, and - importantly - what the integration does *not* do when a record is
deleted. Nothing in the source cascades a delete, so these tests encode the
current "orphans are left dangling" semantics (test plan section 6.3 item 3)
so that adding cascade behaviour later shows up as a deliberate change.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.choremander.coordinator import ChoremanderCoordinator
from custom_components.choremander.models import (
    Child,
    Chore,
    ChoreCompletion,
    Reward,
    RewardClaim,
)


@pytest.fixture(autouse=True)
def _frontend_is_irrelevant_here(bypass_frontend_registration: None) -> None:
    """Every test in this module is about storage CRUD, not the frontend."""
    return None


@pytest.fixture
def save_calls(coordinator: ChoremanderCoordinator, monkeypatch: pytest.MonkeyPatch) -> list[None]:
    """Record every `storage.async_save()` the coordinator performs."""
    calls: list[None] = []
    original = coordinator.storage.async_save

    async def counting_save() -> None:
        calls.append(None)
        await original()

    monkeypatch.setattr(coordinator.storage, "async_save", counting_save)
    return calls


def _rows(coordinator: ChoremanderCoordinator, key: str) -> list[dict[str, Any]]:
    """Raw stored rows for a collection, bypassing the model layer."""
    return coordinator.storage._data.get(key, [])


# --------------------------------------------------------------------------
# Children
# --------------------------------------------------------------------------


async def test_adding_a_child_persists_it_with_an_id_and_zeroed_stats(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A brand new child starts at zero points with a generated id."""
    child = await coordinator.async_add_child("Krishna")

    assert child.id, "async_add_child must hand back a child carrying a generated id"
    stored = coordinator.get_child(child.id)
    assert stored is not None, f"child {child.id} was not written to storage"
    assert stored.name == "Krishna"
    assert stored.avatar == "mdi:account-circle", (
        "a child added without an explicit avatar should get the model default"
    )
    assert (stored.points, stored.total_points_earned, stored.total_chores_completed) == (
        0,
        0,
        0,
    ), "a newly added child must start with no points and no history"
    assert stored.pending_rewards == [] and stored.chore_order == []


async def test_adding_a_child_refreshes_the_coordinator_data(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The refresh inside `async_add_child` makes the child visible immediately."""
    child = await coordinator.async_add_child("Radha", avatar="mdi:cat")

    names = [c.name for c in coordinator.data["children"]]
    assert names == ["Radha"], f"coordinator.data should already show the new child, got {names}"
    assert coordinator.data["children"][0].avatar == "mdi:cat"
    assert coordinator.data["children"][0].id == child.id


async def test_updating_a_child_replaces_the_stored_row(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Edits to a child object are written back under the same id."""
    child = await coordinator.async_add_child("Krishna")

    child.name = "Krishna Kumar"
    child.points = 42
    await coordinator.async_update_child(child)

    stored = coordinator.get_child(child.id)
    assert stored is not None
    assert (stored.name, stored.points) == ("Krishna Kumar", 42), (
        "async_update_child must overwrite the existing row, not append a new one"
    )
    assert len(_rows(coordinator, "children")) == 1, (
        "updating an existing child must not grow the children list"
    )


async def test_updating_an_unknown_child_silently_inserts_it(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`update_child` upserts - there is no "not found" error path."""
    ghost = Child(name="Ghost", id="never_added")

    await coordinator.async_update_child(ghost)

    assert coordinator.get_child("never_added") is not None, (
        "storage.update_child falls through to add_child when the id is absent, "
        "so updating a child that was never added creates it"
    )
    assert len(_rows(coordinator, "children")) == 1


async def test_removing_a_child_deletes_only_that_child(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Removal is scoped to the matching id."""
    keep = await coordinator.async_add_child("Radha")
    drop = await coordinator.async_add_child("Krishna")

    await coordinator.async_remove_child(drop.id)

    remaining = [c.id for c in coordinator.data["children"]]
    assert remaining == [keep.id], (
        f"only {drop.id} should have been removed, children left: {remaining}"
    )
    assert coordinator.get_child(drop.id) is None


async def test_removing_an_unknown_child_is_a_silent_noop(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Deleting a child that does not exist neither raises nor changes anything."""
    keep = await coordinator.async_add_child("Radha")

    await coordinator.async_remove_child("no_such_child")

    assert [c.id for c in coordinator.data["children"]] == [keep.id], (
        "removing a non-existent child must leave the roster untouched"
    )


# --------------------------------------------------------------------------
# Chores
# --------------------------------------------------------------------------


async def test_adding_a_chore_stores_every_model_field(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A chore round-trips through storage with all eleven serialised fields."""
    chore = await coordinator.async_add_chore(
        name="Make Bed",
        points=2,
        description="Duvet straight",
        due_days=["monday"],
        assigned_to=["child_krishna"],
        requires_approval=False,
        time_category="morning",
        daily_limit=3,
        completion_sound="fanfare",
        completion_percentage_per_month=50,
    )

    assert _rows(coordinator, "chores")[0] == {
        "name": "Make Bed",
        "points": 2,
        "description": "Duvet straight",
        "due_days": ["monday"],
        "assigned_to": ["child_krishna"],
        "requires_approval": False,
        "time_category": "morning",
        "daily_limit": 3,
        "completion_sound": "fanfare",
        "completion_percentage_per_month": 50,
        "id": chore.id,
    }, "the stored chore row must carry exactly the eleven Chore.to_dict fields"


async def test_adding_a_chore_defaults_to_ten_points_and_requires_approval(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The bare-minimum call produces an approval-gated 10 point anytime chore."""
    chore = await coordinator.async_add_chore("Vacuum")

    stored = coordinator.get_chore(chore.id)
    assert stored is not None
    assert stored.points == 10
    assert stored.requires_approval is True, (
        "chores default to requiring parent approval before points are awarded"
    )
    assert stored.time_category == "anytime"
    assert stored.daily_limit == 1
    assert stored.completion_sound == "coin"
    assert stored.completion_percentage_per_month == 100
    assert stored.due_days == [] and stored.assigned_to == []


async def test_add_chore_icon_parameter_is_discarded(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`icon` is accepted by the signature but never reaches the Chore model."""
    chore = await coordinator.async_add_chore("Sweep", icon="mdi:rocket")

    assert not hasattr(chore, "icon"), (
        "Chore has no icon field, so async_add_chore's icon argument goes nowhere"
    )
    assert "icon" not in _rows(coordinator, "chores")[0], (
        "no icon key should be persisted for a chore"
    )


async def test_updating_a_chore_replaces_the_stored_row(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Editing points and category writes back under the same chore id."""
    chore = await coordinator.async_add_chore("Dishes", points=4)

    chore.points = 9
    chore.time_category = "evening"
    await coordinator.async_update_chore(chore)

    stored = coordinator.get_chore(chore.id)
    assert stored is not None
    assert (stored.points, stored.time_category) == (9, "evening")
    assert len(_rows(coordinator, "chores")) == 1, (
        "updating an existing chore must not append a duplicate"
    )


async def test_updating_an_unknown_chore_silently_inserts_it(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`update_chore` upserts, exactly like `update_child`."""
    await coordinator.async_update_chore(Chore(name="Ghost Chore", id="never_added"))

    assert coordinator.get_chore("never_added") is not None, (
        "storage.update_chore falls through to add_chore for an unknown id"
    )


async def test_removing_a_chore_deletes_only_that_chore(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Removal is scoped to the matching chore id."""
    keep = await coordinator.async_add_chore("Dishes")
    drop = await coordinator.async_add_chore("Trash")

    await coordinator.async_remove_chore(drop.id)

    assert [c.id for c in coordinator.data["chores"]] == [keep.id], (
        "only the requested chore should disappear"
    )


# --------------------------------------------------------------------------
# Bulk chore creation
# --------------------------------------------------------------------------


async def test_add_chores_bulk_trims_and_skips_blanks(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Whitespace is stripped and empty entries never become chores."""
    created = await coordinator.async_add_chores_bulk(
        ["  Dishes  ", "", "   ", "Trash"], points=6, time_category="evening"
    )

    assert [c.name for c in created] == ["Dishes", "Trash"], (
        "blank and whitespace-only lines must not produce chores, and names "
        f"must be trimmed; got {[c.name for c in created]}"
    )
    assert [c.name for c in coordinator.data["chores"]] == ["Dishes", "Trash"]
    assert all(c.points == 6 and c.time_category == "evening" for c in created), (
        "every chore created in one bulk call shares the same settings"
    )


async def test_add_chores_bulk_forces_an_empty_description(
    coordinator: ChoremanderCoordinator,
) -> None:
    """There is no per-chore description in the bulk path."""
    created = await coordinator.async_add_chores_bulk(["Dishes"])

    assert created[0].description == "", (
        "async_add_chores_bulk hardcodes description='' for every chore it creates"
    )


async def test_add_chores_bulk_skips_save_when_nothing_was_created(
    coordinator: ChoremanderCoordinator, save_calls: list[None]
) -> None:
    """An all-blank list short-circuits before touching storage."""
    created = await coordinator.async_add_chores_bulk(["", "  "])

    assert created == [], "no chore should be created from blank names"
    assert save_calls == [], (
        f"storage must not be saved when no chore was created, saw {len(save_calls)} save(s)"
    )
    assert _rows(coordinator, "chores") == [], "storage should still hold no chores"


async def test_add_chores_bulk_saves_once_for_the_whole_batch(
    coordinator: ChoremanderCoordinator, save_calls: list[None]
) -> None:
    """Three chores cost one save, not three."""
    await coordinator.async_add_chores_bulk(["A", "B", "C"])

    assert len(save_calls) == 1, (
        f"a bulk add should persist once for the batch, saw {len(save_calls)} save(s)"
    )
    assert len(_rows(coordinator, "chores")) == 3


async def test_add_chores_bulk_aliases_one_assigned_to_list_across_every_chore(
    coordinator: ChoremanderCoordinator,
) -> None:
    """All chores in a batch share the caller's list object, and so does storage.

    `assigned_to or []` returns the caller's list unchanged when it is
    non-empty, and `Chore.to_dict()` aliases rather than copies it, so a later
    mutation of the caller's list silently rewrites every stored chore row.
    """
    shared = ["child_krishna"]

    created = await coordinator.async_add_chores_bulk(["Dishes", "Trash"], assigned_to=shared)

    assert created[0].assigned_to is shared and created[1].assigned_to is shared, (
        "both chores must hold the very same list object the caller passed in"
    )

    shared.append("child_radha")

    assigned_rows = [row["assigned_to"] for row in _rows(coordinator, "chores")]
    assert assigned_rows == [
        ["child_krishna", "child_radha"],
        ["child_krishna", "child_radha"],
    ], (
        "mutating the caller's list after the bulk add leaks into every persisted "
        f"chore because the list is aliased, not copied; got {assigned_rows}"
    )


async def test_add_chores_bulk_gives_each_chore_a_distinct_id(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Ids are generated per chore even though settings are shared."""
    created = await coordinator.async_add_chores_bulk(["A", "B", "C"])

    ids = [c.id for c in created]
    assert len(set(ids)) == 3, f"bulk-created chores must not share an id, got {ids}"


# --------------------------------------------------------------------------
# Rewards
# --------------------------------------------------------------------------


async def test_adding_a_reward_stores_every_model_field(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A reward round-trips through storage with all nine serialised fields."""
    reward = await coordinator.async_add_reward(
        name="Zoo Trip",
        cost=150,
        description="Family day out",
        icon="mdi:elephant",
        assigned_to=["child_krishna"],
        is_jackpot=True,
        override_point_value=True,
        days_to_goal=14,
    )

    assert _rows(coordinator, "rewards")[0] == {
        "name": "Zoo Trip",
        "cost": 150,
        "description": "Family day out",
        "icon": "mdi:elephant",
        "assigned_to": ["child_krishna"],
        "is_jackpot": True,
        "override_point_value": True,
        "days_to_goal": 14,
        "id": reward.id,
    }, "the stored reward row must carry exactly the nine Reward.to_dict fields"


async def test_adding_a_reward_defaults_to_dynamic_pricing(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`override_point_value` defaults to False, i.e. the price is calculated."""
    reward = await coordinator.async_add_reward("Screen Time")

    stored = coordinator.get_reward(reward.id)
    assert stored is not None
    assert stored.override_point_value is False, (
        "a reward added through the coordinator keeps dynamic pricing by default"
    )
    assert (stored.cost, stored.days_to_goal, stored.icon) == (50, 30, "mdi:gift")
    assert stored.is_jackpot is False
    assert stored.assigned_to == [], "an unassigned reward is available to every child"


async def test_updating_a_reward_replaces_the_stored_row(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Editing a reward writes back under the same id."""
    reward = await coordinator.async_add_reward("Screen Time", cost=10)

    reward.cost = 25
    reward.is_jackpot = True
    await coordinator.async_update_reward(reward)

    stored = coordinator.get_reward(reward.id)
    assert stored is not None
    assert (stored.cost, stored.is_jackpot) == (25, True)
    assert len(_rows(coordinator, "rewards")) == 1, (
        "updating an existing reward must not append a duplicate"
    )


async def test_updating_an_unknown_reward_silently_inserts_it(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`update_reward` upserts, exactly like children and chores."""
    await coordinator.async_update_reward(Reward(name="Ghost Reward", id="never_added"))

    assert coordinator.get_reward("never_added") is not None, (
        "storage.update_reward falls through to add_reward for an unknown id"
    )


async def test_removing_a_reward_deletes_only_that_reward(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Removal is scoped to the matching reward id."""
    keep = await coordinator.async_add_reward("Screen Time")
    drop = await coordinator.async_add_reward("New Book")

    await coordinator.async_remove_reward(drop.id)

    assert [r.id for r in coordinator.data["rewards"]] == [keep.id], (
        "only the requested reward should disappear"
    )


# --------------------------------------------------------------------------
# Per-child chore order
# --------------------------------------------------------------------------


async def test_set_chore_order_stores_the_list_verbatim_without_validation(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Unknown ids and duplicates are persisted exactly as supplied."""
    child = await coordinator.async_add_child("Krishna")
    real = await coordinator.async_add_chore("Dishes")

    order = [real.id, "not_a_chore", real.id]
    await coordinator.async_set_chore_order(child.id, order)

    stored = coordinator.get_child(child.id)
    assert stored is not None
    assert stored.chore_order == order, (
        "async_set_chore_order performs no membership or duplicate checking; "
        f"expected the list stored verbatim, got {stored.chore_order}"
    )


async def test_set_chore_order_raises_for_an_unknown_child(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Ordering chores for a child that does not exist is an error."""
    with pytest.raises(ValueError, match="Child nope not found"):
        await coordinator.async_set_chore_order("nope", ["chore_a"])


async def test_set_chore_order_replaces_rather_than_merges(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A second call overwrites the previous ordering outright."""
    child = await coordinator.async_add_child("Krishna")

    await coordinator.async_set_chore_order(child.id, ["a", "b"])
    await coordinator.async_set_chore_order(child.id, ["c"])

    stored = coordinator.get_child(child.id)
    assert stored is not None
    assert stored.chore_order == ["c"], (
        "the newest ordering wins outright; nothing is merged with the previous one"
    )


# --------------------------------------------------------------------------
# Coordinator data shape and settings
# --------------------------------------------------------------------------


async def test_update_data_returns_nine_keys_of_model_objects(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A refresh publishes the full nine-key snapshot as parsed model objects."""
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore("Dishes", points=3, requires_approval=True)
    reward = await coordinator.async_add_reward("Screen Time", cost=1, override_point_value=True)
    await coordinator.async_complete_chore(chore.id, child.id)
    await coordinator.async_add_points(child.id, 10)
    await coordinator.async_claim_reward(reward.id, child.id)

    data = coordinator.data
    assert set(data) == {
        "children",
        "chores",
        "rewards",
        "completions",
        "pending_completions",
        "reward_claims",
        "pending_reward_claims",
        "points_name",
        "points_icon",
    }, f"coordinator data keys drifted: {sorted(data)}"

    assert isinstance(data["children"][0], Child)
    assert isinstance(data["chores"][0], Chore)
    assert isinstance(data["rewards"][0], Reward)
    assert isinstance(data["completions"][0], ChoreCompletion)
    assert isinstance(data["pending_completions"][0], ChoreCompletion)
    assert isinstance(data["reward_claims"][0], RewardClaim)
    assert isinstance(data["pending_reward_claims"][0], RewardClaim)
    assert isinstance(data["points_name"], str)
    assert isinstance(data["points_icon"], str)


async def test_set_points_settings_persists_and_refreshes(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Renaming the currency reaches storage and the published data in one call."""
    await coordinator.async_set_points_settings("Coins", "mdi:cash")

    assert coordinator.storage.get_points_name() == "Coins"
    assert coordinator.storage.get_points_icon() == "mdi:cash"
    assert coordinator.data["points_name"] == "Coins", (
        "async_set_points_settings refreshes, so the new currency name is "
        "visible in coordinator.data without a further update cycle"
    )
    assert coordinator.data["points_icon"] == "mdi:cash"


# --------------------------------------------------------------------------
# Orphaning - nothing cascades (test plan section 6.3 item 3)
# --------------------------------------------------------------------------


async def test_removing_a_child_does_not_cascade_to_completions_or_claims(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Deleting a child leaves their history behind pointing at a dead id.

    # CURRENT BEHAVIOUR (test plan section 6.3 item 3) - deletes never cascade.
    # This test pins today's semantics so adding cleanup later is a deliberate,
    # test-visible change.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore("Dishes", points=10, requires_approval=False)
    reward = await coordinator.async_add_reward("Screen Time", cost=10, override_point_value=True)
    await coordinator.async_complete_chore(chore.id, child.id)
    await coordinator.async_claim_reward(reward.id, child.id)

    await coordinator.async_remove_child(child.id)

    assert coordinator.get_child(child.id) is None, "the child itself must be gone"
    orphan_completions = [c for c in coordinator.data["completions"] if c.child_id == child.id]
    orphan_claims = [c for c in coordinator.data["reward_claims"] if c.child_id == child.id]
    assert len(orphan_completions) == 1, (
        "the completion survives the child's deletion and still names the dead child id"
    )
    assert len(orphan_claims) == 1, (
        "the reward claim survives the child's deletion and still names the dead child id"
    )


async def test_removing_a_child_leaves_dangling_assignments_on_chores_and_rewards(
    coordinator: ChoremanderCoordinator,
) -> None:
    """`assigned_to` lists are never scrubbed when a child is deleted.

    # CURRENT BEHAVIOUR (test plan section 6.3 item 3) - deletes never cascade.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore("Dishes", assigned_to=[child.id])
    reward = await coordinator.async_add_reward("Screen Time", assigned_to=[child.id])

    await coordinator.async_remove_child(child.id)

    stored_chore = coordinator.get_chore(chore.id)
    stored_reward = coordinator.get_reward(reward.id)
    assert stored_chore is not None and stored_chore.assigned_to == [child.id], (
        "the chore still claims to be assigned to a child that no longer exists"
    )
    assert stored_reward is not None and stored_reward.assigned_to == [child.id], (
        "the reward still claims to be assigned to a child that no longer exists"
    )


async def test_a_reward_assigned_only_to_a_deleted_child_prices_to_nothing(
    coordinator: ChoremanderCoordinator,
) -> None:
    """The dangling assignment makes the reward unpriceable for anyone."""
    child = await coordinator.async_add_child("Krishna")
    await coordinator.async_add_chore("Dishes", points=5)
    reward = await coordinator.async_add_reward(
        "Screen Time", cost=30, assigned_to=[child.id], days_to_goal=4
    )
    assert coordinator.calculate_dynamic_reward_costs(reward) == {child.id: 20}, (
        "sanity check: 5 pts/day at 100% over 4 days prices the reward at 20"
    )

    await coordinator.async_remove_child(child.id)

    stale = coordinator.get_reward(reward.id)
    assert stale is not None
    assert coordinator.calculate_dynamic_reward_costs(stale) == {}, (
        "with its only assigned child deleted the reward resolves to no assigned "
        "children at all, so no child - not even a new one - can be quoted a price"
    )


async def test_removing_a_chore_leaves_its_completions_orphaned(
    coordinator: ChoremanderCoordinator,
) -> None:
    """Completion history outlives the chore it refers to.

    # CURRENT BEHAVIOUR (test plan section 6.3 item 3) - deletes never cascade.
    """
    child = await coordinator.async_add_child("Krishna")
    chore = await coordinator.async_add_chore("Dishes", points=7, requires_approval=True)
    completion = await coordinator.async_complete_chore(chore.id, child.id)

    await coordinator.async_remove_chore(chore.id)

    assert coordinator.get_chore(chore.id) is None, "the chore itself must be gone"
    surviving = [c.id for c in coordinator.data["completions"]]
    assert surviving == [completion.id], (
        "the completion record survives and still points at a deleted chore"
    )
    assert coordinator.data["pending_completions"][0].chore_id == chore.id, (
        "the orphan is still queued for approval even though the chore is gone"
    )


async def test_removing_a_chore_leaves_it_in_every_childs_chore_order(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A deleted chore id stays in the saved ordering.

    # CURRENT BEHAVIOUR (test plan section 6.3 item 3) - deletes never cascade.
    """
    child = await coordinator.async_add_child("Krishna")
    keep = await coordinator.async_add_chore("Trash")
    drop = await coordinator.async_add_chore("Dishes")
    await coordinator.async_set_chore_order(child.id, [drop.id, keep.id])

    await coordinator.async_remove_chore(drop.id)

    stored = coordinator.get_child(child.id)
    assert stored is not None
    assert stored.chore_order == [drop.id, keep.id], (
        "the deleted chore id is left in chore_order; nothing prunes it"
    )


async def test_removing_a_reward_leaves_its_claims_orphaned_and_unrefundable(
    coordinator: ChoremanderCoordinator,
) -> None:
    """A pending claim survives its reward, and rejecting it refunds nothing.

    # CURRENT BEHAVIOUR (test plan section 6.3 item 3) - deletes never cascade.
    # `async_reject_reward` guards on `if reward and child`, so once the reward
    # is gone the points spent on the claim can never come back.
    """
    child = await coordinator.async_add_child("Krishna")
    await coordinator.async_add_points(child.id, 30)
    reward = await coordinator.async_add_reward("Screen Time", cost=30, override_point_value=True)
    claim = await coordinator.async_claim_reward(reward.id, child.id)
    assert coordinator.get_child(child.id).points == 0, (
        "sanity check: claiming a 30 point reward with exactly 30 points empties the balance"
    )

    await coordinator.async_remove_reward(reward.id)

    assert [c.id for c in coordinator.data["reward_claims"]] == [claim.id], (
        "the claim survives the reward's deletion and still names the dead reward id"
    )

    await coordinator.async_reject_reward(claim.id)

    assert coordinator.get_child(child.id).points == 0, (
        "rejecting a claim whose reward was deleted refunds nothing - the child's "
        "points are lost permanently"
    )
    assert coordinator.data["reward_claims"] == [], (
        "the claim is still deleted even though no refund was possible"
    )
