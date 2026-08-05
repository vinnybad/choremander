"""Sensor platform for Choremander integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from datetime import datetime

import logging

from .activity import build_activity_feed
from .const import ACTIVITY_FEED_LIMIT, DOMAIN
from .coordinator import ChoremanderCoordinator
from .models import Child, Chore, Reward

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Choremander sensors."""
    coordinator: ChoremanderCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []

    # Track child IDs that have sensors created
    tracked_child_ids: set[str] = set()

    # Add overall stats sensor
    entities.append(ChoremandorOverallStatsSensor(coordinator, entry))

    # Add sensors for each child
    for child in coordinator.data.get("children", []):
        entities.append(ChildPointsSensor(coordinator, entry, child))
        entities.append(ChildStatsSensor(coordinator, entry, child))
        tracked_child_ids.add(child.id)

    # Add pending approvals sensor
    entities.append(PendingApprovalsSensor(coordinator, entry))

    # Add activity feed and streak sensors
    entities.append(ActivitySensor(coordinator, entry))
    entities.append(StreaksSensor(coordinator, entry))

    async_add_entities(entities)

    # Set up listener for new children
    @callback
    def async_add_child_sensors() -> None:
        """Add sensors for newly added children."""
        new_entities: list[SensorEntity] = []

        for child in coordinator.data.get("children", []):
            if child.id not in tracked_child_ids:
                new_entities.append(ChildPointsSensor(coordinator, entry, child))
                new_entities.append(ChildStatsSensor(coordinator, entry, child))
                tracked_child_ids.add(child.id)

        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_listener(async_add_child_sensors)


class ChoremandorBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Choremander sensors."""

    def __init__(
        self,
        coordinator: ChoremandorCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="Choremander",
            manufacturer="Choremander",
            model="Family Chore Manager",
        )


class ChoremandorOverallStatsSensor(ChoremandorBaseSensor):
    """Sensor for overall Choremander statistics."""

    def __init__(
        self,
        coordinator: ChoremandorCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_overall_stats"
        self._attr_name = "Choremander Overview"

    @property
    def native_value(self) -> int:
        """Return the total number of children."""
        return len(self.coordinator.data.get("children", []))

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        data = self.coordinator.data
        children = data.get("children", [])
        chores = data.get("chores", [])
        rewards = data.get("rewards", [])

        total_points = sum(c.points for c in children)
        total_chores_completed = sum(c.total_chores_completed for c in children)

        # Get all completions and pending completions
        all_completions = data.get("completions", [])
        pending_completions = data.get("pending_completions", [])

        # Build today's completions summary (both approved and pending)
        # Use HA timezone-aware datetime for proper date comparison
        now = dt_util.now()
        today = now.date()
        todays_completions = []
        for comp in all_completions:
            # Convert completion time to HA timezone for proper date comparison
            comp_dt = comp.completed_at
            if hasattr(comp_dt, 'astimezone'):
                # If timezone-aware, convert to HA timezone
                comp_dt = dt_util.as_local(comp_dt)
            comp_date = comp_dt.date() if hasattr(comp_dt, 'date') else comp_dt
            if comp_date == today:
                todays_completions.append({
                    "completion_id": comp.id,
                    "chore_id": comp.chore_id,
                    "child_id": comp.child_id,
                    "approved": comp.approved,
                    "completed_at": comp.completed_at.isoformat() if hasattr(comp.completed_at, 'isoformat') else str(comp.completed_at),
                })

        # Calculate pending points per child
        pending_points_by_child = {}
        for comp in pending_completions:
            child_id = comp.child_id
            chore = next((c for c in chores if c.id == comp.chore_id), None)
            if chore:
                pending_points_by_child[child_id] = pending_points_by_child.get(child_id, 0) + chore.points

        # Build chores list with explicit assigned_to handling
        chores_list = []
        for c in chores:
            # Ensure assigned_to is always a list
            assigned_to = c.assigned_to if isinstance(c.assigned_to, list) else []
            chores_list.append({
                "id": c.id,
                "name": c.name,
                "points": c.points,
                "time_category": c.time_category,
                "daily_limit": getattr(c, 'daily_limit', 1),
                "assigned_to": assigned_to,
                "completion_sound": getattr(c, 'completion_sound', 'coin'),
                "completion_percentage_per_month": getattr(c, 'completion_percentage_per_month', 100),
            })

        # Build rewards list with per-child dynamic cost calculation
        rewards_list = []
        for r in rewards:
            # Get reward fields with defaults
            override_point_value = getattr(r, 'override_point_value', False)
            days_to_goal = getattr(r, 'days_to_goal', 30)

            # Calculate the cost per child - use dynamic calculation if available
            try:
                if hasattr(self.coordinator, 'calculate_dynamic_reward_costs'):
                    calculated_costs = self.coordinator.calculate_dynamic_reward_costs(r)
                else:
                    # Fallback: use static cost for all assigned children
                    assigned = r.assigned_to if isinstance(r.assigned_to, list) and r.assigned_to else [c.id for c in children]
                    calculated_costs = {child_id: r.cost for child_id in assigned}
            except Exception as e:
                # If calculation fails, use static cost
                _LOGGER.error("SENSOR: Error calculating costs for %s: %s", r.name, e)
                assigned = r.assigned_to if isinstance(r.assigned_to, list) and r.assigned_to else [c.id for c in children]
                calculated_costs = {child_id: r.cost for child_id in assigned}

            # For jackpot rewards, get each child's daily expected points for weighted meter
            child_daily_points = {}
            if getattr(r, 'is_jackpot', False) and hasattr(self.coordinator, 'get_child_daily_points'):
                try:
                    child_daily_points = self.coordinator.get_child_daily_points(r)
                except Exception as e:
                    _LOGGER.error("SENSOR: Error getting daily points for %s: %s", r.name, e)

            rewards_list.append({
                "id": r.id,
                "name": r.name,
                "cost": r.cost,  # Manual/override cost
                "description": getattr(r, 'description', ''),
                "icon": r.icon,
                "assigned_to": r.assigned_to if isinstance(r.assigned_to, list) else [],
                "is_jackpot": getattr(r, 'is_jackpot', False),
                "override_point_value": override_point_value,
                "days_to_goal": days_to_goal,
                "calculated_costs": calculated_costs,  # Dict of child_id -> cost
                "child_daily_points": child_daily_points,  # Dict of child_id -> daily expected points (for weighted jackpot meter)
            })

        return {
            "total_children": len(children),
            "total_chores": len(chores),
            "total_rewards": len(rewards),
            "total_points_available": total_points,
            "total_chores_completed": total_chores_completed,
            "points_name": data.get("points_name", "Stars"),
            "points_icon": data.get("points_icon", "mdi:star"),
            # Deliberately lean. This payload already carries every chore and
            # every reward with its per-child calculated costs, which grows as
            # children x rewards, and tests/unit/test_sensor_payload_size.py
            # shows a large household leaving little room under the recorder's
            # 16KiB attribute ceiling. Streak and lifetime figures live on
            # sensor.choremander_streaks instead of being added here.
            "children": [
                {
                    "id": c.id,
                    "name": c.name,
                    "points": c.points,
                    "pending_points": pending_points_by_child.get(c.id, 0),
                    "chore_order": c.chore_order,
                }
                for c in children
            ],
            "chores": chores_list,
            "rewards": rewards_list,
            "todays_completions": todays_completions,
            "total_completions_all_time": len(all_completions),
            "total_pending_completions": len(pending_completions),
        }

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:clipboard-check-multiple"


class ChildPointsSensor(ChoremandorBaseSensor):
    """Sensor for a child's points."""

    def __init__(
        self,
        coordinator: ChoremandorCoordinator,
        entry: ConfigEntry,
        child: Child,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.child_id = child.id
        self._attr_unique_id = f"{entry.entry_id}_{child.id}_points"
        self._attr_name = f"{child.name} Points"
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> int:
        """Return the child's current points."""
        child = self.coordinator.get_child(self.child_id)
        return child.points if child else 0

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return self.coordinator.data.get("points_name", "Stars")

    @property
    def icon(self) -> str:
        """Return the icon."""
        return self.coordinator.data.get("points_icon", "mdi:star")

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        child = self.coordinator.get_child(self.child_id)
        if not child:
            return {}

        return {
            "child_id": child.id,
            "child_name": child.name,
            "avatar": child.avatar,
            "total_points_earned": child.total_points_earned,
            "total_chores_completed": child.total_chores_completed,
            "current_streak": child.current_streak,
            "best_streak": child.best_streak,
        }


class ChildStatsSensor(ChoremandorBaseSensor):
    """Sensor for a child's statistics."""

    def __init__(
        self,
        coordinator: ChoremandorCoordinator,
        entry: ConfigEntry,
        child: Child,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self.child_id = child.id
        self._attr_unique_id = f"{entry.entry_id}_{child.id}_stats"
        self._attr_name = f"{child.name} Stats"
        self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self) -> int:
        """Return the child's total chores completed."""
        child = self.coordinator.get_child(self.child_id)
        return child.total_chores_completed if child else 0

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "chores"

    @property
    def icon(self) -> str:
        """Return the icon."""
        child = self.coordinator.get_child(self.child_id)
        return child.avatar if child else "mdi:account-circle"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        child = self.coordinator.get_child(self.child_id)
        if not child:
            return {}

        # Get chores assigned to this child
        chores = self.coordinator.data.get("chores", [])
        assigned_chores = [c for c in chores if child.id in c.assigned_to or not c.assigned_to]

        return {
            "child_id": child.id,
            "child_name": child.name,
            "avatar": child.avatar,
            "points": child.points,
            "total_points_earned": child.total_points_earned,
            "total_chores_completed": child.total_chores_completed,
            "current_streak": child.current_streak,
            "best_streak": child.best_streak,
            "assigned_chores": [{"id": c.id, "name": c.name, "points": c.points, "time_category": c.time_category} for c in assigned_chores],
            "chore_order": child.chore_order,
        }


class PendingApprovalsSensor(ChoremandorBaseSensor):
    """Sensor for pending approvals."""

    def __init__(
        self,
        coordinator: ChoremandorCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_pending_approvals"
        self._attr_name = "Pending Approvals"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of pending approvals."""
        pending_completions = self.coordinator.data.get("pending_completions", [])
        pending_rewards = self.coordinator.data.get("pending_reward_claims", [])
        return len(pending_completions) + len(pending_rewards)

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:clipboard-clock"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        pending_completions = self.coordinator.data.get("pending_completions", [])
        pending_rewards = self.coordinator.data.get("pending_reward_claims", [])

        completion_details = []
        for comp in pending_completions:
            child = self.coordinator.get_child(comp.child_id)
            chore = self.coordinator.get_chore(comp.chore_id)
            if child and chore:
                completion_details.append({
                    "completion_id": comp.id,
                    "type": "chore",
                    "child_name": child.name,
                    "child_id": child.id,
                    "chore_name": chore.name,
                    "chore_id": chore.id,
                    "points": chore.points,
                    "time_category": chore.time_category,
                    "completed_at": comp.completed_at.isoformat(),
                })

        reward_details = []
        for claim in pending_rewards:
            child = self.coordinator.get_child(claim.child_id)
            reward = self.coordinator.get_reward(claim.reward_id)
            if child and reward:
                reward_details.append({
                    "claim_id": claim.id,
                    "type": "reward",
                    "child_name": child.name,
                    "child_id": child.id,
                    "reward_name": reward.name,
                    "reward_id": reward.id,
                    "cost": reward.cost,
                    "claimed_at": claim.claimed_at.isoformat(),
                })

        return {
            "pending_chore_completions": len(pending_completions),
            "pending_reward_claims": len(pending_rewards),
            "chore_completions": completion_details,
            "reward_claims": reward_details,
        }


class ActivitySensor(ChoremandorBaseSensor):
    """Sensor carrying the family's recent activity.

    This lives on its own entity rather than being folded into the overview
    sensor. The overview payload already carries every chore and reward with
    their per-child calculated costs, and Home Assistant's recorder drops any
    state whose attributes exceed 16KiB. Growing an already-large attribute set
    with an unbounded feed is how that limit gets hit; a separate entity keeps
    the two budgets independent.
    """

    def __init__(
        self,
        coordinator: ChoremandorCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_activity"
        self._attr_name = "Choremander Activity"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of events in the published feed."""
        return len(self._feed())

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "events"

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:timeline-text"

    def _feed(self) -> list[dict]:
        """Build the activity feed from the coordinator's current data."""
        data = self.coordinator.data or {}
        return build_activity_feed(
            transactions=data.get("points_transactions", []),
            pending_completions=data.get("pending_completions", []),
            children=data.get("children", []),
            chores=data.get("chores", []),
            rewards=data.get("rewards", []),
            limit=ACTIVITY_FEED_LIMIT,
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        data = self.coordinator.data or {}
        return {
            "events": self._feed(),
            "points_name": data.get("points_name", "Stars"),
            "points_icon": data.get("points_icon", "mdi:star"),
            "children": [
                {"id": c.id, "name": c.name, "avatar": c.avatar}
                for c in data.get("children", [])
            ],
        }


class StreaksSensor(ChoremandorBaseSensor):
    """Sensor carrying each child's streak and lifetime figures.

    Separate from the overview sensor for the same reason the activity feed is:
    the overview payload grows with children x rewards and has the least room
    to spare, while this one grows only with the number of children.

    The streak numbers here are derived from the approved completion history on
    every refresh rather than read from storage, so they cannot drift out of
    step with the chores that produced them.
    """

    def __init__(
        self,
        coordinator: ChoremandorCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_streaks"
        self._attr_name = "Choremander Streaks"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the longest streak currently running in the household."""
        children = (self.coordinator.data or {}).get("children", [])
        return max((c.current_streak for c in children), default=0)

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "days"

    @property
    def icon(self) -> str:
        """Return the icon."""
        return "mdi:fire"

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional attributes."""
        data = self.coordinator.data or {}
        streaks = data.get("streaks", {})
        return {
            "children": [
                {
                    "id": c.id,
                    "name": c.name,
                    "avatar": c.avatar,
                    "current_streak": c.current_streak,
                    "best_streak": c.best_streak,
                    "total_chores_completed": c.total_chores_completed,
                    "total_points_earned": c.total_points_earned,
                    # Only the days the child was actually active inside the
                    # window. The card knows the window and fills in the gaps,
                    # which keeps this proportional to activity rather than to
                    # the length of the window.
                    "active_days": streaks.get(c.id, {}).get("active_days", []),
                }
                for c in data.get("children", [])
            ],
        }
