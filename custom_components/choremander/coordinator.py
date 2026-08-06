"""Data coordinator for Choremander integration."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
import logging
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    ACTIVE_DAYS_WINDOW,
    DOMAIN,
    EVENT_CHORE_APPROVED,
    EVENT_CHORE_REJECTED,
    EVENT_POINTS_ADDED,
    EVENT_POINTS_REMOVED,
    EVENT_REWARD_CLAIMED,
    EVENT_REWARD_REFUNDED,
    MAX_REASON_LENGTH,
)
from .models import Child, Chore, ChoreCompletion, PointsTransaction, Reward, RewardClaim
from .schedule import ChoreSchedule, assignees_on, prune_date_overrides
from .storage import ChoremanderStorage
from .streaks import compute_streaks, recent_active_days

_LOGGER = logging.getLogger(__name__)

# How far ahead pricing looks to work out how often a chore actually falls to a
# child. Four whole weeks, so a week rotation between two children comes out at
# exactly half rather than at whichever side of half the window happened to end
# on. Looking *forward* rather than back is deliberate: a reward's price is a
# promise about what the child can earn from now on, so a rotation that starts
# next week, or a one-off swap a parent has already entered, has to count.
PRICING_WINDOW_DAYS: Final = 28


class ChoremanderCoordinator(DataUpdateCoordinator):
    """Coordinator to manage Choremander data."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.storage = ChoremanderStorage(hass, entry_id)
        self.entry_id = entry_id

    async def async_initialize(self) -> None:
        """Initialize the coordinator."""
        await self.storage.async_load()
        await self.async_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from storage."""
        children = self.storage.get_children()
        completions = self.storage.get_completions()
        streaks = self._build_streak_summary(children, completions)

        # The stored streak fields are not trusted - they are recomputed here
        # from the completion history on every refresh, so this is the value
        # every sensor and card sees. See streaks.py for why.
        for child in children:
            self._apply_streak(child, streaks)

        return {
            "children": children,
            "chores": self.storage.get_chores(),
            "rewards": self.storage.get_rewards(),
            "completions": completions,
            "pending_completions": [c for c in completions if not c.approved],
            "reward_claims": self.storage.get_reward_claims(),
            "pending_reward_claims": self.storage.get_pending_reward_claims(),
            "points_transactions": self.storage.get_points_transactions(),
            "points_name": self.storage.get_points_name(),
            "points_icon": self.storage.get_points_icon(),
            "streaks": streaks,
        }

    # Schedule
    #
    # Everything about *when* a chore falls and *whose* turn it is lives in
    # schedule.py. These two methods are the only bridge between it and the
    # rest of the integration, and they stay deliberately thin: the sensor, the
    # pricing loop and the streak rest-day set all come through here, which is
    # what stops three slightly different answers to "is this chore on today?"
    # from appearing.
    def chore_assignees_on(
        self, chore: Chore, day: date, household: list[str] | None = None
    ) -> list[str]:
        """Return the ids of the children on ``chore`` on ``day``.

        The one thing this adds to ``schedule.assignees_on`` is the household
        roster, which that module cannot know and deliberately does not want to:
        a chore with an empty ``assigned_to`` means *anyone*, which is how this
        product has always read it and what every card and the pricing loop
        assume. Substituting the roster *before* resolving rather than patching
        an empty result afterwards matters twice over - an empty result is also
        how a genuine day off is expressed, and an unassigned chore that also
        rotates should hand round the whole family rather than resolve to
        nobody at all.

        ``household`` is an optional pre-computed roster. Callers resolving many
        days in a row should pass it; rebuilding every ``Child`` out of storage
        once per chore per day is the difference between a cheap loop and a
        noticeable one on a 28-day pricing window.
        """
        if household is None:
            household = self._household_ids()
        return assignees_on(self._chore_schedule(chore, household), day)

    def chore_ids_for_child_on(self, child_id: str, day: date) -> list[str]:
        """Return the ids of the chores ``child_id`` is on for ``day``.

        The inverse question, answered by asking the forward one about every
        chore rather than by a second implementation of the rules. Order follows
        storage order, not the child's ``chore_order``; sorting for display is
        the card's job and it already does it.
        """
        household = self._household_ids()
        return [
            chore.id
            for chore in self.storage.get_chores()
            if child_id in self.chore_assignees_on(chore, day, household)
        ]

    def _household_ids(self) -> list[str]:
        """Every child id in the house, in roster order."""
        return [child.id for child in self.storage.get_children()]

    def _chore_schedule(self, chore: Chore, household: list[str]) -> ChoreSchedule:
        """Lift a chore's schedule, standing the household in for 'anyone'."""
        schedule = ChoreSchedule.from_chore(chore)
        if schedule.assigned_to:
            return schedule
        return replace(schedule, assigned_to=list(household))

    # Streaks
    def _build_streak_summary(
        self,
        children: list[Child],
        completions: list[ChoreCompletion],
    ) -> dict[str, dict[str, Any]]:
        """Derive every child's streak figures from the completion history.

        A day counts if the child *did the work* on it, approved or not. Waiting
        on a parent must not break a streak: a child who does their chores every
        day but whose parent only approves at the weekend would otherwise watch
        their streak read zero for days and then snap back to five, which is
        both wrong and impossible to explain.

        Rejected work does not keep a streak alive either, and needs no special
        handling here - rejecting a completion deletes the record, so the day
        simply stops being active on the next refresh. That self-correction is
        the whole reason for deriving these figures instead of counting them.

        A day the child had *nothing scheduled* is neither. Without that, the
        moment a family puts a chore on Monday to Saturday every child loses
        their streak every Sunday, and "I did everything you asked and still
        lost my streak" is precisely the experience this product exists to
        avoid. Those days go to ``compute_streaks`` as rest days, where they
        neither extend a run nor break it.
        """
        today = dt_util.now().date()

        active_days: dict[str, set[date]] = {}
        for completion in completions:
            # Bucket by the day it was local time in this house, not UTC, or a
            # chore done at 8pm in a negative-offset timezone lands on tomorrow.
            local_day = dt_util.as_local(completion.completed_at).date()
            active_days.setdefault(completion.child_id, set()).add(local_day)

        rest_days = self._rest_days(children, today)

        summary: dict[str, dict[str, Any]] = {}
        for child in children:
            days = active_days.get(child.id, set())
            # A day off the child worked anyway is a day they worked. Leaving it
            # in both sets would publish it to the card as a muted "day off" dot
            # despite there being a completion behind it.
            rest = rest_days.get(child.id, set()) - days
            current, best = compute_streaks(days, today, rest_days=frozenset(rest))
            summary[child.id] = {
                "current_streak": current,
                "best_streak": best,
                "active_days": recent_active_days(days, today, ACTIVE_DAYS_WINDOW),
                "rest_days": sorted(day.isoformat() for day in rest),
            }
        return summary

    def _rest_days(self, children: list[Child], today: date) -> dict[str, set[date]]:
        """Find each child's scheduled days off inside the streak window.

        Bounded to ``ACTIVE_DAYS_WINDOW`` - the same window the dot strip draws -
        rather than to all of history. A run that reaches back further than the
        strip is broken by any gap beyond it exactly as it was before schedules
        existed, which is a bounded, explicable inaccuracy; walking a child's
        whole history through the schedule engine on every thirty-second
        refresh, to change a number nothing displays, is not worth it.

        Resolved from the schedule as it stands *now*, so editing a rule
        reshapes past rest days as well as future ones. That is the same bet
        ``streaks.py`` and ``schedule.py`` both take deliberately: snapshotting
        a roster per day would reintroduce exactly the drift those modules exist
        to prevent.
        """
        window = [today - timedelta(days=offset) for offset in range(ACTIVE_DAYS_WINDOW)]
        household = self._household_ids()

        scheduled: dict[str, set[date]] = {}
        for chore in self.storage.get_chores():
            # Resolved once per chore per day and then read for every child,
            # rather than once per child, so the cost of this is independent of
            # the size of the family.
            schedule = self._chore_schedule(chore, household)
            for day in window:
                for child_id in assignees_on(schedule, day):
                    scheduled.setdefault(child_id, set()).add(day)

        return {
            child.id: {day for day in window if day not in scheduled.get(child.id, set())}
            for child in children
        }

    def _apply_streak(
        self, child: Child, streaks: dict[str, dict[str, Any]] | None = None
    ) -> None:
        """Overwrite a child's streak fields with the derived values."""
        if streaks is None:
            streaks = (self.data or {}).get("streaks") or {}
        summary = streaks.get(child.id)
        if summary is None:
            # No cached summary yet - first load, or a child added since the
            # last refresh. Derive just this one.
            summary = self._build_streak_summary(
                [child], self.storage.get_completions()
            )[child.id]
        child.current_streak = summary["current_streak"]
        child.best_streak = summary["best_streak"]

    # Points ledger
    def _record_transaction(
        self,
        child_id: str,
        points: int,
        event_type: str,
        reason: str = "",
        chore_id: str = "",
        reward_id: str = "",
        related_id: str = "",
    ) -> None:
        """Append a ledger entry describing a change to a child's balance.

        ``points`` is the amount the balance actually moved by, which is not
        always the amount that was asked for - balances are clamped at zero, so
        removing 50 points from a child who has 20 is recorded as -20. The
        ledger has to agree with the balance it explains.
        """
        if not points and event_type in (EVENT_POINTS_ADDED, EVENT_POINTS_REMOVED):
            # A manual adjustment of nothing is not an event; there is neither a
            # movement to account for nor anything a parent needs to see. A
            # zero-point *chore* is still worth recording, because the activity
            # feed exists to answer "I did this, where is it?" - and a chore
            # that vanished on approval would answer it badly.
            return
        self.storage.add_points_transaction(
            PointsTransaction(
                child_id=child_id,
                points=points,
                event_type=event_type,
                created_at=dt_util.now(),
                # Free text a parent typed, so it is unbounded at the service
                # boundary. The whole feed shares one entity's 16KiB attribute
                # budget, and exceeding it makes Home Assistant stop recording
                # the entity with no error anywhere.
                reason=reason[:MAX_REASON_LENGTH],
                chore_id=chore_id,
                reward_id=reward_id,
                related_id=related_id,
            )
        )

    # Child operations
    async def async_add_child(self, name: str, avatar: str = "mdi:account-circle") -> Child:
        """Add a new child."""
        child = Child(name=name, avatar=avatar)
        self.storage.add_child(child)
        await self.storage.async_save()
        await self.async_refresh()
        return child

    async def async_update_child(self, child: Child) -> None:
        """Update a child."""
        self.storage.update_child(child)
        await self.storage.async_save()
        await self.async_refresh()

    async def async_remove_child(self, child_id: str) -> None:
        """Remove a child."""
        self.storage.remove_child(child_id)
        await self.storage.async_save()
        await self.async_refresh()

    def get_child(self, child_id: str) -> Child | None:
        """Get a child by ID, with derived streak figures applied."""
        child = self.storage.get_child(child_id)
        if child is not None:
            self._apply_streak(child)
        return child

    # Chore operations
    async def async_add_chore(
        self,
        name: str,
        points: int = 10,
        description: str = "",
        icon: str = "mdi:broom",
        due_days: list[str] | None = None,
        assigned_to: list[str] | None = None,
        requires_approval: bool = True,
        time_category: str = "anytime",
        daily_limit: int = 1,
        completion_sound: str = "coin",
        completion_percentage_per_month: int = 100,
    ) -> Chore:
        """Add a new chore."""
        chore = Chore(
            name=name,
            points=points,
            description=description,
            due_days=due_days or [],
            assigned_to=assigned_to or [],
            requires_approval=requires_approval,
            time_category=time_category,
            daily_limit=daily_limit,
            completion_sound=completion_sound,
            completion_percentage_per_month=completion_percentage_per_month,
        )
        self.storage.add_chore(chore)
        await self.storage.async_save()
        await self.async_refresh()
        return chore

    async def async_add_chores_bulk(
        self,
        chore_names: list[str],
        points: int = 10,
        icon: str = "mdi:broom",
        due_days: list[str] | None = None,
        assigned_to: list[str] | None = None,
        requires_approval: bool = True,
        time_category: str = "anytime",
        daily_limit: int = 1,
        completion_sound: str = "coin",
        completion_percentage_per_month: int = 100,
    ) -> list[Chore]:
        """Add multiple chores at once with shared settings."""
        chores = []
        for name in chore_names:
            name = name.strip()
            if not name:
                continue
            chore = Chore(
                name=name,
                points=points,
                description="",
                due_days=due_days or [],
                assigned_to=assigned_to or [],
                requires_approval=requires_approval,
                time_category=time_category,
                daily_limit=daily_limit,
                completion_sound=completion_sound,
                completion_percentage_per_month=completion_percentage_per_month,
            )
            self.storage.add_chore(chore)
            chores.append(chore)

        if chores:
            await self.storage.async_save()
            await self.async_refresh()
        return chores

    async def async_update_chore(self, chore: Chore) -> None:
        """Update a chore."""
        self.storage.update_chore(chore)
        await self.storage.async_save()
        await self.async_refresh()

    async def async_set_chore_schedule(
        self,
        chore_id: str,
        due_days: list[str] | None = None,
        rotation: str | None = None,
        rotation_order: list[str] | None = None,
        rotation_anchor: str | None = None,
        day_overrides: dict[str, list[str]] | None = None,
        date_overrides: dict[str, list[str]] | None = None,
    ) -> Chore:
        """Write the scheduling rule for one chore.

        Every field is optional and ``None`` means *leave it alone*, which is
        not the same as clearing it: the schedule card edits one row of the grid
        at a time and a call that only moves Saturday must not silently reset a
        rotation the parent set last week. Clearing is asking for it explicitly
        with ``[]`` or ``{}``.

        Separate from ``async_update_chore`` rather than folded into it because
        the two have different writers. The options flow rebuilds a whole chore
        from a form that has no schedule fields on it, so letting it write the
        chore wholesale is exactly how a rotation gets wiped by someone editing
        the points.

        Nothing here validates: an unknown rotation mode, a weekday that is not
        a weekday and a child id that has since left the family are all stored
        as sent, and ``schedule.py`` degrades gracefully on every one of them.
        Rejecting them would put a second, quietly different set of rules in
        front of the one that actually decides who does the dishes.
        """
        chore = self.get_chore(chore_id)
        if not chore:
            raise ValueError(f"Chore {chore_id} not found")

        if due_days is not None:
            chore.due_days = list(due_days)
        if rotation is not None:
            chore.rotation = rotation
        if rotation_order is not None:
            chore.rotation_order = list(rotation_order)
        if rotation_anchor is not None:
            chore.rotation_anchor = rotation_anchor
        if day_overrides is not None:
            chore.day_overrides = dict(day_overrides)

        # Pruned on every call, not only when overrides were supplied. A family
        # that swaps one Saturday a week accumulates one dead key a week
        # forever, and the write path is the only place that can actually shrink
        # the stored blob - pruning on read would hide the growth while letting
        # it continue.
        chore.date_overrides = prune_date_overrides(
            chore.date_overrides if date_overrides is None else date_overrides,
            dt_util.now().date(),
        )

        self.storage.update_chore(chore)
        await self.storage.async_save()
        await self.async_refresh()
        return chore

    async def async_remove_chore(self, chore_id: str) -> None:
        """Remove a chore."""
        self.storage.remove_chore(chore_id)
        await self.storage.async_save()
        await self.async_refresh()

    def get_chore(self, chore_id: str) -> Chore | None:
        """Get a chore by ID."""
        return self.storage.get_chore(chore_id)

    # Reward operations
    def calculate_dynamic_reward_costs(self, reward: Reward) -> dict[str, int]:
        """Calculate the dynamic cost of a reward for each child.

        By default, all rewards use dynamic pricing. If override_point_value is True,
        the manual cost is used instead.

        For non-jackpot rewards, each child has their own calculated cost based on
        their specific chores and completion rates.

        For jackpot rewards, all children share the same cost (sum of all daily points).

        Returns:
            Dict mapping child_id to their calculated cost for this reward.
            For jackpot rewards, all children have the same value.
        """
        result: dict[str, int] = {}

        # Get all children and chores
        all_children = self.storage.get_children()
        all_chores = self.storage.get_chores()

        _LOGGER.debug(
            "CALC_COSTS: reward=%s, override=%s, days_to_goal=%s, cost=%s, assigned_to=%s",
            reward.name,
            getattr(reward, 'override_point_value', False),
            getattr(reward, 'days_to_goal', 30),
            reward.cost,
            reward.assigned_to
        )
        _LOGGER.debug("CALC_COSTS: found %d children, %d chores", len(all_children), len(all_chores))

        # Determine which children are assigned to this reward
        if reward.assigned_to:
            assigned_children = [c for c in all_children if c.id in reward.assigned_to]
        else:
            assigned_children = all_children

        _LOGGER.debug("calculate_dynamic_reward_costs: assigned_children=%s", [c.name for c in assigned_children])

        if not assigned_children:
            _LOGGER.debug("calculate_dynamic_reward_costs: no assigned children, returning empty")
            return result

        # If override is enabled, use the manual cost for all children
        if getattr(reward, 'override_point_value', False):
            _LOGGER.debug("calculate_dynamic_reward_costs: override enabled, using manual cost %d", reward.cost)
            for child in assigned_children:
                result[child.id] = reward.cost
            return result

        # Get days_to_goal with a sensible default
        days_to_goal = getattr(reward, 'days_to_goal', 30)
        if days_to_goal <= 0:
            days_to_goal = 30

        if not all_chores:
            # Fall back to static cost if no chores
            _LOGGER.debug("calculate_dynamic_reward_costs: no chores, falling back to cost %d", reward.cost)
            for child in assigned_children:
                result[child.id] = reward.cost
            return result

        # Calculate daily expected points for each child
        child_daily_points = self._daily_expected_points(assigned_children, all_chores)
        for child in assigned_children:
            _LOGGER.debug(
                "CALC_COSTS: child=%s, daily_points=%.2f",
                child.name, child_daily_points.get(child.id, 0.0)
            )

        # Calculate costs based on whether this is a jackpot reward
        if reward.is_jackpot:
            # Jackpot: sum of ALL children's daily points, same cost for everyone
            total_daily_points = sum(child_daily_points.values())
            jackpot_cost = max(1, round(total_daily_points * days_to_goal))
            _LOGGER.debug(
                "calculate_dynamic_reward_costs: jackpot total_daily=%.2f, cost=%d",
                total_daily_points, jackpot_cost
            )
            for child in assigned_children:
                result[child.id] = jackpot_cost
        else:
            # Non-jackpot: each child has their own cost based on their chores
            for child in assigned_children:
                daily_pts = child_daily_points.get(child.id, 0)
                if daily_pts > 0:
                    calculated = max(1, round(daily_pts * days_to_goal))
                    result[child.id] = calculated
                    _LOGGER.debug(
                        "calculate_dynamic_reward_costs: child=%s, daily=%.2f * days=%d = %d",
                        child.name, daily_pts, days_to_goal, calculated
                    )
                else:
                    result[child.id] = reward.cost  # Fall back to manual cost
                    _LOGGER.debug(
                        "calculate_dynamic_reward_costs: child=%s, no daily points, fallback to %d",
                        child.name, reward.cost
                    )

        _LOGGER.debug("CALC_COSTS: final result for %s = %s", reward.name, result)
        return result

    def get_child_daily_points(self, reward: Reward) -> dict[str, float]:
        """Get the daily expected points for each child assigned to a reward.

        This is what the jackpot meter draws its per-child contributions from,
        so it goes through the same ``_daily_expected_points`` the price does. A
        meter that filled at a different rate than the goal it was filling
        towards would be a bug the arithmetic could never be talked out of.
        """
        all_children = self.storage.get_children()
        all_chores = self.storage.get_chores()

        # Determine which children are assigned to this reward
        if reward.assigned_to:
            assigned_children = [c for c in all_children if c.id in reward.assigned_to]
        else:
            assigned_children = all_children

        if not assigned_children or not all_chores:
            return {}

        return self._daily_expected_points(assigned_children, all_chores)

    def _daily_expected_points(
        self, children: list[Child], chores: list[Chore]
    ) -> dict[str, float]:
        """Return what each child is expected to earn per day, on average.

        The one number every dynamic price is built out of::

            points x (scheduled_days_per_week / 7) x (completion_pct / 100)

        The middle factor is what scheduling added. Without it a parent who puts
        the bins out on Mondays gets a reward priced as though the bins went out
        every day, the goal quietly moves seven times further away, and nothing
        anywhere says why. That is the trap this whole calculation exists to
        avoid, and it is silent, which is what makes it dangerous.

        A chore with no schedule falls on every one of the window's days, so the
        factor is exactly ``1.0`` and the result is bit-for-bit the number this
        function returned before schedules existed - the property the pricing
        tests pin explicitly, because every installed household is in that case.

        The frequency is measured rather than read off ``due_days``: a rotation
        splits a chore's days between children, an override adds or removes one,
        and counting the days the child is actually named is the only reading
        that survives all of them. It counts resolved days over
        ``PRICING_WINDOW_DAYS`` and divides, so ``days_on / 28`` *is*
        ``days_per_week / 7`` with no separate weekly bookkeeping.

        Never divides by anything but that constant, so a chore that falls on
        nobody contributes nothing rather than raising.
        """
        today = dt_util.now().date()
        household = self._household_ids()
        window = [today + timedelta(days=offset) for offset in range(PRICING_WINDOW_DAYS)]

        rates: dict[str, float] = {child.id: 0.0 for child in children}
        for chore in chores:
            # Resolved once per chore per day and then read for every child.
            # This runs for every reward on every sensor rebuild, and doing it
            # per child instead would multiply that by the size of the family
            # for an identical answer.
            schedule = self._chore_schedule(chore, household)
            days_on: dict[str, int] = {}
            for day in window:
                # Deduplicated: an override or rotation order that names the
                # same child twice is one day on the chore, not two, and an
                # undeduplicated count could push the factor above 1.0.
                for child_id in set(assignees_on(schedule, day)):
                    days_on[child_id] = days_on.get(child_id, 0) + 1

            completion_pct = chore.completion_percentage_per_month
            for child_id in rates:
                scheduled = days_on.get(child_id, 0)
                if not scheduled:
                    # Not this child's chore, or not on in the next four weeks.
                    # Skipping rather than adding zero keeps the float sum
                    # identical to the pre-scheduling one.
                    continue
                frequency = scheduled / PRICING_WINDOW_DAYS
                rates[child_id] += chore.points * frequency * (completion_pct / 100)

        return rates

    def calculate_dynamic_reward_cost(self, reward: Reward, child_id: str | None = None) -> int:
        """Calculate the dynamic cost of a reward.

        For backward compatibility. If child_id is provided, returns that child's cost.
        Otherwise returns the first child's cost or the manual cost.
        """
        costs = self.calculate_dynamic_reward_costs(reward)

        if child_id and child_id in costs:
            return costs[child_id]

        if costs:
            return list(costs.values())[0]

        return reward.cost

    async def async_add_reward(
        self,
        name: str,
        cost: int = 50,
        description: str = "",
        icon: str = "mdi:gift",
        assigned_to: list[str] | None = None,
        is_jackpot: bool = False,
        override_point_value: bool = False,
        days_to_goal: int = 30,
    ) -> Reward:
        """Add a new reward."""
        reward = Reward(
            name=name,
            cost=cost,
            description=description,
            icon=icon,
            assigned_to=assigned_to or [],
            is_jackpot=is_jackpot,
            override_point_value=override_point_value,
            days_to_goal=days_to_goal,
        )
        self.storage.add_reward(reward)
        await self.storage.async_save()
        await self.async_refresh()
        return reward

    async def async_update_reward(self, reward: Reward) -> None:
        """Update a reward."""
        self.storage.update_reward(reward)
        await self.storage.async_save()
        await self.async_refresh()

    async def async_remove_reward(self, reward_id: str) -> None:
        """Remove a reward."""
        self.storage.remove_reward(reward_id)
        await self.storage.async_save()
        await self.async_refresh()

    def get_reward(self, reward_id: str) -> Reward | None:
        """Get a reward by ID."""
        return self.storage.get_reward(reward_id)

    # Chore completion operations
    async def async_complete_chore(self, chore_id: str, child_id: str) -> ChoreCompletion:
        """Mark a chore as completed by a child."""
        chore = self.get_chore(chore_id)
        if not chore:
            raise ValueError(f"Chore {chore_id} not found")

        child = self.get_child(child_id)
        if not child:
            raise ValueError(f"Child {child_id} not found")

        # Check daily limit - count today's completions for this chore by this child
        # Both pending (unapproved) and approved completions count toward the limit
        now = dt_util.now()
        today = now.date()
        all_completions = self.storage.get_completions()
        todays_completions_count = 0
        for comp in all_completions:
            if comp.chore_id == chore_id and comp.child_id == child_id:
                # Convert to local timezone for date comparison
                comp_dt = comp.completed_at
                if hasattr(comp_dt, 'astimezone'):
                    comp_dt = dt_util.as_local(comp_dt)
                comp_date = comp_dt.date() if hasattr(comp_dt, 'date') else comp_dt
                if comp_date == today:
                    todays_completions_count += 1

        daily_limit = getattr(chore, 'daily_limit', 1)
        if todays_completions_count >= daily_limit:
            raise ValueError(
                f"Daily limit reached for chore '{chore.name}'. "
                f"Already completed {todays_completions_count} time(s) today (limit: {daily_limit})"
            )

        completion = ChoreCompletion(
            chore_id=chore_id,
            child_id=child_id,
            completed_at=now,
            approved=not chore.requires_approval,
            points_awarded=chore.points if not chore.requires_approval else 0,
        )

        # If no approval required, award points immediately
        if not chore.requires_approval:
            await self._award_points(child, chore.points)
            completion.approved = True
            completion.approved_at = dt_util.now()
            completion.points_awarded = chore.points
            self._record_transaction(
                child_id=child_id,
                points=chore.points,
                event_type=EVENT_CHORE_APPROVED,
                chore_id=chore_id,
                related_id=completion.id,
            )

        self.storage.add_completion(completion)
        await self.storage.async_save()
        await self.async_refresh()
        return completion

    async def async_approve_chore(self, completion_id: str) -> None:
        """Approve a chore completion."""
        completions = self.storage.get_completions()
        for completion in completions:
            if completion.id == completion_id:
                if completion.approved:
                    # Approving twice would award the points twice. Two parents
                    # tapping the same approval, or a double-tap on a slow
                    # connection, is enough to hit this.
                    _LOGGER.debug(
                        "Completion %s is already approved; ignoring", completion_id
                    )
                    return

                chore = self.get_chore(completion.chore_id)
                child = self.get_child(completion.child_id)

                if chore and child:
                    completion.approved = True
                    completion.approved_at = dt_util.now()
                    completion.points_awarded = chore.points
                    await self._award_points(child, chore.points)
                    self.storage.update_completion(completion)
                    self._record_transaction(
                        child_id=child.id,
                        points=chore.points,
                        event_type=EVENT_CHORE_APPROVED,
                        chore_id=chore.id,
                        related_id=completion.id,
                    )
                    await self.storage.async_save()
                    await self.async_refresh()
                return

    async def async_reject_chore(self, completion_id: str) -> None:
        """Reject a chore completion and deduct points if they were already awarded."""
        completions = self.storage.get_completions()
        for completion in completions:
            if completion.id == completion_id:
                # Gated on `approved`, not on `points_awarded > 0`. Approval is
                # what moved anything: it credits the balance *and* increments
                # the lifetime chore count, and it does the latter even for a
                # chore worth zero points. Keying the rollback on the points
                # instead left a zero-point chore permanently counted as work
                # done - which now unlocks streak-card milestones - and left its
                # row in the activity feed with nothing to say it was undone.
                if completion.approved:
                    child = self.get_child(completion.child_id)
                    if child:
                        # Record what the balance actually moved by. A child
                        # who has spent the points since cannot go negative, so
                        # the clawback can be smaller than the original award.
                        before = child.points
                        child.points = max(0, before - completion.points_awarded)
                        # The lifetime counters have to come back too. They now
                        # drive the streak card's milestones, so a rejected
                        # chore that left them alone would permanently overstate
                        # how much work a child had done.
                        child.total_points_earned = max(
                            0, child.total_points_earned - completion.points_awarded
                        )
                        child.total_chores_completed = max(
                            0, child.total_chores_completed - 1
                        )
                        self.storage.update_child(child)
                        self._record_transaction(
                            child_id=child.id,
                            points=child.points - before,
                            event_type=EVENT_CHORE_REJECTED,
                            chore_id=completion.chore_id,
                            related_id=completion.id,
                        )
                break

        self.storage.remove_completion(completion_id)
        await self.storage.async_save()
        await self.async_refresh()

    # Reward claim operations
    async def async_claim_reward(self, reward_id: str, child_id: str) -> RewardClaim:
        """Child claims a reward."""
        reward = self.get_reward(reward_id)
        if not reward:
            raise ValueError(f"Reward {reward_id} not found")

        child = self.get_child(child_id)
        if not child:
            raise ValueError(f"Child {child_id} not found")

        # Get the effective cost for this child (dynamic or override)
        costs = self.calculate_dynamic_reward_costs(reward)
        effective_cost = costs.get(child_id, reward.cost)

        if child.points < effective_cost:
            raise ValueError(f"Not enough points. Need {effective_cost}, have {child.points}")

        claim = RewardClaim(
            reward_id=reward_id,
            child_id=child_id,
            claimed_at=dt_util.now(),
        )

        # Deduct points immediately using the effective cost
        child.points -= effective_cost
        self.storage.update_child(child)
        self._record_transaction(
            child_id=child_id,
            points=-effective_cost,
            event_type=EVENT_REWARD_CLAIMED,
            reward_id=reward_id,
            related_id=claim.id,
        )

        self.storage.add_reward_claim(claim)
        await self.storage.async_save()
        await self.async_refresh()
        return claim

    async def async_approve_reward(self, claim_id: str) -> None:
        """Approve a reward claim."""
        claims = self.storage.get_reward_claims()
        for claim in claims:
            if claim.id == claim_id:
                claim.approved = True
                claim.approved_at = dt_util.now()
                self.storage.update_reward_claim(claim)
                await self.storage.async_save()
                await self.async_refresh()
                return

    async def async_reject_reward(self, claim_id: str) -> None:
        """Reject a reward claim and refund points."""
        claims = self.storage.get_reward_claims()
        for claim in claims:
            if claim.id == claim_id:
                reward = self.get_reward(claim.reward_id)
                child = self.get_child(claim.child_id)
                if reward and child:
                    # Refund points using the effective cost for this child
                    costs = self.calculate_dynamic_reward_costs(reward)
                    effective_cost = costs.get(claim.child_id, reward.cost)
                    child.points += effective_cost
                    self.storage.update_child(child)
                    self._record_transaction(
                        child_id=child.id,
                        points=effective_cost,
                        event_type=EVENT_REWARD_REFUNDED,
                        reward_id=reward.id,
                        related_id=claim.id,
                    )
                self.storage.remove_reward_claim(claim_id)
                await self.storage.async_save()
                await self.async_refresh()
                return

    # Points operations
    async def async_add_points(self, child_id: str, points: int, reason: str = "") -> None:
        """Add points to a child (bonus)."""
        child = self.get_child(child_id)
        if not child:
            raise ValueError(f"Child {child_id} not found")
        before = child.points
        await self._award_points(child, points, count_as_chore=False)
        # The movement, not the request. They are equal today because nothing
        # clamps an award, but the ledger's contract is about what happened.
        self._record_transaction(
            child_id=child_id,
            points=child.points - before,
            event_type=EVENT_POINTS_ADDED,
            reason=reason,
        )
        await self.storage.async_save()
        await self.async_refresh()

    async def async_remove_points(self, child_id: str, points: int, reason: str = "") -> None:
        """Remove points from a child (penalty)."""
        child = self.get_child(child_id)
        if not child:
            raise ValueError(f"Child {child_id} not found")
        before = child.points
        child.points = max(0, before - points)
        self.storage.update_child(child)
        self._record_transaction(
            child_id=child_id,
            points=child.points - before,
            event_type=EVENT_POINTS_REMOVED,
            reason=reason,
        )
        await self.storage.async_save()
        await self.async_refresh()

    async def _award_points(
        self, child: Child, points: int, count_as_chore: bool = True
    ) -> None:
        """Award points to a child.

        ``count_as_chore`` exists because a manual bonus is not a chore. Adding
        one used to increment the lifetime chore counter, which quietly
        overstated how much work a child had actually done.
        """
        child.points += points
        child.total_points_earned += points
        if count_as_chore:
            child.total_chores_completed += 1

        self.storage.update_child(child)

    # Child chore order operations
    async def async_set_chore_order(self, child_id: str, chore_order: list[str]) -> None:
        """Set the chore order for a child."""
        child = self.get_child(child_id)
        if not child:
            raise ValueError(f"Child {child_id} not found")

        child.chore_order = chore_order
        self.storage.update_child(child)
        await self.storage.async_save()
        await self.async_refresh()

    # Settings
    async def async_set_points_settings(self, name: str, icon: str) -> None:
        """Update points settings."""
        self.storage.set_points_name(name)
        self.storage.set_points_icon(icon)
        await self.storage.async_save()
        await self.async_refresh()
