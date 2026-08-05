"""A regression guard on the size of every Choremander sensor's attributes.

Home Assistant's recorder refuses to store a state whose attributes exceed
**16KiB**. When that happens the entity does not raise - it simply stops being
recorded, so history, statistics and anything reading back through the recorder
go quietly blank. There is no runtime signal at all; the first anyone knows is a
user reporting that their graphs are empty. Nothing else in this repository can
notice that, so these tests are the only alarm.

The household below is deliberately larger and busier than a typical family -
five children, twenty-five chores, ten rewards, a completely full thirty-day
`active_days` window each, a completely full `ACTIVITY_FEED_LIMIT` activity feed
and a heavy day of completions - but every number in it is one a real household
could reach. Time is frozen so the measurements are byte-stable and a failure
message can be trusted to the byte.

Three jobs, done by three families of test:

* `..._stays_under_the_recorder_attribute_limit` is the hard line. Failing it
  means the integration is already broken for that household.
* `..._keeps_a_comfortable_headroom_margin` fails much earlier, at 75%, so there
  is room to react before anything breaks in the field. **The fix when it fails
  is to move data onto a companion entity, never to raise the threshold** - the
  ceiling is a recorder database column limit, and `ActivitySensor` exists as its
  own entity for exactly this reason.
* `..._does_not_grow_any_further` ratchets the one sensor that has *already*
  spent its headroom (see `KNOWN_CROWDED`) and now sits at 99% of the ceiling, so
  the known problem cannot quietly get worse while a fix is decided.

The measurements as of writing, for `-s` output see the last test in this file:

    16213 bytes   99.0%  sensor.choremander_overview   <- no headroom left
     9447 bytes   57.7%  sensor.choremander_activity
     4938 bytes   30.1%  sensor.pending_approvals
     2953 bytes   18.0%  sensor.choremander_streaks
    ~2000 bytes   ~12%   sensor.<child>_stats  (x5)
     ~270 bytes    ~2%   sensor.<child>_points (x5)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.choremander.const import (
    ACTIVE_DAYS_WINDOW,
    ACTIVITY_FEED_LIMIT,
    DOMAIN,
    EVENT_CHORE_APPROVED,
    EVENT_CHORE_REJECTED,
    EVENT_POINTS_ADDED,
    EVENT_POINTS_REMOVED,
    EVENT_REWARD_CLAIMED,
    EVENT_REWARD_REFUNDED,
)

# The recorder drops any state whose attributes exceed this. It is a database
# column limit in `homeassistant.components.recorder`, not a tunable, so the only
# lever available to us is how much a single entity carries.
MAX_ATTR_BYTES = 16384

# How much of that budget one sensor may occupy before this suite complains.
# Deliberately well short of the ceiling: the payload grows with the household,
# and a family that gains a child must not tip over between two releases.
HEADROOM_FRACTION = 0.75
HEADROOM_BYTES = int(MAX_ATTR_BYTES * HEADROOM_FRACTION)

# Sensors already past the headroom margin, though still under the hard limit.
# Every entry is measured debt, not a suppression: the hard-limit test above
# applies to these too. See
# `test_the_overview_payload_is_dominated_by_four_list_keys` for where the bytes
# go. This set must only ever shrink.
#
# sensor.choremander_overview sits at ~99% of the ceiling for five children. That
# predates this guard - it is what the overview has always published - and the
# cause is structural: `rewards[].calculated_costs` carries a per-child cost map
# on every reward, so the payload grows as children x rewards. Splitting it means
# changing the shape the rewards card reads, which is deliberate work of its own
# rather than something to fold into an unrelated change.
KNOWN_CROWDED = frozenset({"sensor.choremander_overview"})

# The overview measures 16,213 bytes under this household - inside the limit,
# with almost nothing to spare. The ratchet holds that line so an unrelated
# change cannot be the one that tips it over.
OVERVIEW_RATCHET_BYTES = 16384

ENTRY_ID = "stress_entry"
STORE_KEY = f"choremander.storage.{ENTRY_ID}"

# Time is frozen so `active_days` and `todays_completions` - the two keys whose
# size depends on the wall clock - are filled identically on every run, and so
# the byte counts quoted above stay true. Mid-June avoids both daylight saving
# transitions, keeping the thirty-day window free of any 23- or 25-hour day.
FROZEN_NOW = "2026-06-15T18:00:00+00:00"

# Sizing of the stress household. Each figure is at or above what a real family
# would plausibly have, so passing here is a genuine margin.
CHILD_COUNT = 5
CHORE_COUNT = 25
REWARD_COUNT = 10
LEDGER_ENTRIES = 120  # three times what the feed will publish
COMPLETIONS_PER_DAY_PER_CHILD = 4
PENDING_PER_CHILD = 3

CHILD_NAMES = ["Krishna", "Radha", "Arjun", "Meera", "Ananya"]

# Realistic, unabbreviated chore names. A payload guard built on "c1".."c25"
# would only be flattering itself.
CHORE_NAMES = [
    ("Make the Bed", "morning", 2),
    ("Brush Teeth (Morning)", "morning", 1),
    ("Get Dressed for School", "morning", 2),
    ("Feed the Dog", "morning", 3),
    ("Water the House Plants", "morning", 2),
    ("Empty the Dishwasher", "morning", 4),
    ("Pack Your School Lunch Box", "morning", 3),
    ("Take the Recycling Out", "afternoon", 3),
    ("Sweep the Kitchen Floor", "afternoon", 4),
    ("Fold and Put Away Laundry", "afternoon", 5),
    ("Walk the Dog Around the Block", "afternoon", 5),
    ("Wipe Down the Bathroom Sink", "afternoon", 3),
    ("Practise the Piano", "evening", 4),
    ("Do Your Homework", "evening", 5),
    ("Read for Twenty Minutes", "evening", 3),
    ("Set the Table for Dinner", "evening", 2),
    ("Clear the Table After Dinner", "evening", 2),
    ("Load the Dishwasher", "evening", 4),
    ("Brush Teeth (Evening)", "evening", 1),
    ("Tidy Your Bedroom Floor", "evening", 3),
    ("Put Your Shoes on the Rack", "evening", 1),
    ("Take a Shower Without Reminding", "night", 2),
    ("Lay Out Tomorrow's Clothes", "night", 2),
    ("Charge the Tablet in the Kitchen", "night", 1),
    ("Say Goodnight to Everyone", "night", 1),
]

# (name, icon, cost, is_jackpot, override_point_value, days_to_goal)
REWARD_NAMES = [
    ("Thirty Minutes of Extra Screen Time", "mdi:television", 10, False, False, 7),
    ("Choose Tonight's Dinner Menu", "mdi:silverware-fork-knife", 20, False, False, 7),
    ("A Brand New Video Game", "mdi:gamepad-variant", 50, False, True, 30),
    ("A New Paperback Book of Your Choice", "mdi:book-open-variant", 30, False, True, 30),
    ("Stay Up an Hour Past Bedtime", "mdi:sleep-off", 25, False, False, 14),
    ("Family Movie Night With Popcorn", "mdi:movie-open", 75, True, False, 14),
    ("A Day Trip to the City Zoo", "mdi:elephant", 150, True, False, 30),
    ("Ice Cream From the Shop on the Corner", "mdi:ice-cream", 15, False, False, 7),
    ("A Friend Over for a Weekend Sleepover", "mdi:tent", 60, False, True, 30),
    ("Pizza Night, Toppings of Your Choosing", "mdi:pizza", 40, True, False, 21),
]

# Free text a parent would really type, at the length they would really type it.
# A third of the ledger carries none, which is what makes the omit-when-empty
# rule in activity.py worth having.
LEDGER_REASONS = [
    "",
    "",
    "Helped carry the groceries in without being asked",
    "Was unkind to her brother at the dinner table",
    "Bonus for tidying the whole playroom on a Saturday",
    "Left the bathroom in a state again",
]


def _iso(moment: datetime) -> str:
    """Format an instant the way storage does."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _child_id(index: int) -> str:
    return f"child_{CHILD_NAMES[index].lower()}"


def _chore_id(index: int) -> str:
    return f"chore_{index:02d}_{CHORE_NAMES[index][0].split()[0].lower()}"


def _reward_id(index: int) -> str:
    return f"reward_{index:02d}_{REWARD_NAMES[index][0].split()[0].lower()}"


def _stress_household(local_now: datetime) -> dict[str, Any]:
    """Build a storage payload larger than a typical family's.

    `local_now` must be Home Assistant *local* time, not UTC. The two keys that
    grow fastest are both windowed on the local calendar: `active_days` publishes
    only days inside the last `ACTIVE_DAYS_WINDOW`, and `todays_completions` only
    rows landing on today's local date. Anchoring on UTC would let a run in the
    evening of a negative-offset timezone fall outside both windows and quietly
    measure a much smaller payload. Both windows are filled completely here.
    """
    children = [
        {
            "id": _child_id(index),
            "name": CHILD_NAMES[index],
            "points": 25 + index * 17,
            "avatar": "mdi:account-circle",
            "total_points_earned": 480 + index * 31,
            "total_chores_completed": 120 + index * 7,
            # A full custom ordering, as any child whose card has been dragged
            # around once will have.
            "chore_order": [_chore_id(i) for i in range(CHORE_COUNT)],
            "pending_rewards": [],
        }
        for index in range(CHILD_COUNT)
    ]

    chores = []
    for index, (name, category, points) in enumerate(CHORE_NAMES):
        # A realistic mix: some chores belong to everyone, some to a pair, some
        # to one child. Unassigned chores are the expensive case for the reward
        # pricing, so keep plenty of them.
        if index % 3 == 0:
            assigned: list[str] = []
        elif index % 3 == 1:
            assigned = [_child_id(index % CHILD_COUNT)]
        else:
            assigned = [
                _child_id(index % CHILD_COUNT),
                _child_id((index + 2) % CHILD_COUNT),
            ]
        chores.append(
            {
                "id": _chore_id(index),
                "name": name,
                "points": points,
                "description": "",
                "time_category": category,
                "assigned_to": assigned,
                "requires_approval": index % 4 == 0,
                "daily_limit": 1,
                "completion_sound": "coin",
                "completion_percentage_per_month": 100,
            }
        )

    rewards = [
        {
            "id": _reward_id(index),
            "name": name,
            "cost": cost,
            "description": "",
            "icon": icon,
            "assigned_to": [] if index % 2 == 0 else [_child_id(index % CHILD_COUNT)],
            "is_jackpot": jackpot,
            "override_point_value": override,
            "days_to_goal": days,
        }
        for index, (name, icon, cost, jackpot, override, days) in enumerate(REWARD_NAMES)
    ]

    # A busy day for every child on every day of the active-days window, so
    # `active_days` comes back completely full for all five children. Midday
    # local keeps every completion on the local date it was generated for.
    completions: list[dict[str, Any]] = []
    for day_offset in range(ACTIVE_DAYS_WINDOW):
        day = (local_now - timedelta(days=day_offset)).replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        for child_index in range(CHILD_COUNT):
            for slot in range(COMPLETIONS_PER_DAY_PER_CHILD):
                chore_index = (day_offset * 3 + child_index * 5 + slot) % CHORE_COUNT
                moment = day + timedelta(minutes=slot * 37)
                completions.append(
                    {
                        "id": f"comp_{day_offset:02d}_{child_index}_{slot}",
                        "chore_id": _chore_id(chore_index),
                        "child_id": _child_id(child_index),
                        "completed_at": _iso(moment),
                        "approved": True,
                        "approved_at": _iso(moment + timedelta(minutes=5)),
                        "points_awarded": CHORE_NAMES[chore_index][2],
                    }
                )

    # This evening's still-unapproved work, which lands in the activity feed, the
    # pending-approvals sensor and today's completions all at once.
    this_evening = local_now.replace(hour=17, minute=0, second=0, microsecond=0)
    for child_index in range(CHILD_COUNT):
        for slot in range(PENDING_PER_CHILD):
            chore_index = (child_index * 7 + slot * 3) % CHORE_COUNT
            completions.append(
                {
                    "id": f"comp_pending_{child_index}_{slot}",
                    "chore_id": _chore_id(chore_index),
                    "child_id": _child_id(child_index),
                    "completed_at": _iso(this_evening + timedelta(minutes=slot * 11)),
                    "approved": False,
                    "approved_at": None,
                    "points_awarded": 0,
                }
            )

    # Reward claims, half approved and half still waiting on a parent.
    reward_claims = [
        {
            "id": f"claim_{index:02d}",
            "reward_id": _reward_id((index * 3) % REWARD_COUNT),
            "child_id": _child_id(index % CHILD_COUNT),
            "claimed_at": _iso(local_now - timedelta(hours=index * 5)),
            "approved": index % 2 == 0,
            "approved_at": (
                _iso(local_now - timedelta(hours=index * 5 - 1)) if index % 2 == 0 else None
            ),
        }
        for index in range(CHILD_COUNT * 2)
    ]

    # The ledger. Every event type appears, two thirds of the entries carry free
    # text, and there are three times as many entries as the feed will publish -
    # so the feed is bounded by its own limit rather than by the data.
    event_cycle = [
        EVENT_CHORE_APPROVED,
        EVENT_CHORE_APPROVED,
        EVENT_CHORE_APPROVED,
        EVENT_CHORE_REJECTED,
        EVENT_REWARD_CLAIMED,
        EVENT_POINTS_ADDED,
        EVENT_POINTS_REMOVED,
        EVENT_REWARD_REFUNDED,
    ]
    transactions = []
    for index in range(LEDGER_ENTRIES):
        event_type = event_cycle[index % len(event_cycle)]
        chore_index = (index * 7) % CHORE_COUNT
        reward_index = (index * 3) % REWARD_COUNT
        is_reward = event_type in (EVENT_REWARD_CLAIMED, EVENT_REWARD_REFUNDED)
        transactions.append(
            {
                "id": f"txn_{index:04d}",
                "child_id": _child_id(index % CHILD_COUNT),
                "points": (
                    -REWARD_NAMES[reward_index][2]
                    if event_type == EVENT_REWARD_CLAIMED
                    else CHORE_NAMES[chore_index][2]
                ),
                "event_type": event_type,
                # Newest last, exactly as the storage buffer holds them.
                "created_at": _iso(local_now - timedelta(minutes=(LEDGER_ENTRIES - index) * 23)),
                "reason": LEDGER_REASONS[index % len(LEDGER_REASONS)],
                "chore_id": "" if is_reward else _chore_id(chore_index),
                "reward_id": _reward_id(reward_index) if is_reward else "",
                "related_id": f"comp_{index:04d}",
            }
        )

    return {
        "children": children,
        "chores": chores,
        "rewards": rewards,
        "completions": completions,
        "reward_claims": reward_claims,
        "points_transactions": transactions,
        "points_name": "Stars",
        "points_icon": "mdi:star",
    }


@pytest.fixture
async def stressed_entry(
    hass: HomeAssistant, hass_storage: dict, bypass_frontend_registration, freezer
) -> MockConfigEntry:
    """Set up Choremander over the stress household at a frozen instant."""
    freezer.move_to(FROZEN_NOW)
    hass_storage[STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": _stress_household(dt_util.now()),
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Choremander",
        data={"points_name": "Stars", "points_icon": "mdi:star"},
        entry_id=ENTRY_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _encoded_size(value: Any) -> int:
    """The byte length of `value` once JSON-encoded, as the recorder stores it."""
    return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))


def _sensor_payloads(hass: HomeAssistant) -> dict[str, int]:
    """Return `{entity_id: encoded attribute size}` for every Choremander sensor.

    Measured off the live state rather than off `extra_state_attributes`, because
    the recorder stores the whole attribute dict - including the `friendly_name`,
    `icon`, `unit_of_measurement` and `state_class` Home Assistant adds.
    """
    registry = er.async_get(hass)
    entity_ids = sorted(
        entity.entity_id
        for entity in er.async_entries_for_config_entry(registry, ENTRY_ID)
        if entity.domain == "sensor"
    )
    assert entity_ids, "no Choremander sensors were created, so nothing was measured"

    sizes: dict[str, int] = {}
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        assert state is not None, f"{entity_id} is in the registry but has no state"
        sizes[entity_id] = _encoded_size(dict(state.attributes))
    return sizes


def _report(sizes: dict[str, int]) -> str:
    """Every measurement, largest first, for an assertion message."""
    return ", ".join(
        f"{entity_id} = {size} bytes ({size / MAX_ATTR_BYTES:.0%} of budget)"
        for entity_id, size in sorted(sizes.items(), key=lambda item: -item[1])
    )


# ---------------------------------------------------------------------------
# The household really is stressful
# ---------------------------------------------------------------------------


async def test_the_stress_household_actually_exercises_every_growth_dimension(
    hass: HomeAssistant, stressed_entry: MockConfigEntry
) -> None:
    """A size guard is worthless if the payload it measures turns out to be small.

    Everything that makes an attribute payload grow is pinned here, so a change
    that quietly stops filling one of these dimensions cannot make the limits
    below pass by measuring less than they used to.
    """
    overview = hass.states.get("sensor.choremander_overview").attributes
    activity = hass.states.get("sensor.choremander_activity").attributes

    assert overview["total_children"] == CHILD_COUNT
    assert overview["total_chores"] == CHORE_COUNT
    assert overview["total_rewards"] == REWARD_COUNT

    assert len(activity["events"]) == ACTIVITY_FEED_LIMIT, (
        "the ledger must be deep enough to fill the feed to its cap, or the guard "
        f"measures a short feed; got {len(activity['events'])} of {ACTIVITY_FEED_LIMIT}"
    )

    # `active_days` is the widest key on the streaks sensor, and it only reaches
    # its worst case when a child has been active on every day of the window -
    # which is exactly what the household is built to produce.
    streaks = hass.states.get("sensor.choremander_streaks").attributes
    assert len(streaks["children"]) == CHILD_COUNT
    for row in streaks["children"]:
        assert len(row["active_days"]) == ACTIVE_DAYS_WINDOW, (
            f"{row['name']} must have a completely full {ACTIVE_DAYS_WINDOW}-day dot "
            f"strip, which is the widest that key ever gets; got "
            f"{len(row['active_days'])} days"
        )

    # `chore_order` stayed on the overview sensor when the streak figures moved
    # off it, and it is the widest per-child key remaining there.
    for row in overview["children"]:
        assert len(row["chore_order"]) == CHORE_COUNT, (
            "every child carries a full custom chore ordering, the worst case for that key"
        )

    assert overview["total_pending_completions"] == CHILD_COUNT * PENDING_PER_CHILD
    assert len(overview["todays_completions"]) == CHILD_COUNT * (
        COMPLETIONS_PER_DAY_PER_CHILD + PENDING_PER_CHILD
    ), (
        "today's completions must cover a full day of work for every child, approved "
        f"and pending alike; got {len(overview['todays_completions'])}"
    )

    pending = hass.states.get("sensor.pending_approvals").attributes
    assert len(pending["chore_completions"]) == CHILD_COUNT * PENDING_PER_CHILD
    assert pending["reward_claims"], "some reward claims must be awaiting approval too"


# ---------------------------------------------------------------------------
# The hard limit
# ---------------------------------------------------------------------------


async def test_every_sensor_stays_under_the_recorder_attribute_limit(
    hass: HomeAssistant, stressed_entry: MockConfigEntry
) -> None:
    """No Choremander sensor may publish more than 16KiB of attributes.

    Past this line the recorder stops storing the state: no exception, no log
    line a user will ever see, just history and statistics that go blank for that
    entity. Nothing in Home Assistant will tell us, which is why the assertion
    reports exact byte counts - a future failure should say how close we got, not
    merely that we got too close.
    """
    sizes = _sensor_payloads(hass)

    oversized = {entity_id: size for entity_id, size in sizes.items() if size >= MAX_ATTR_BYTES}
    assert not oversized, (
        f"these sensors exceed the recorder's {MAX_ATTR_BYTES}-byte attribute limit "
        "under the stress household and will be silently dropped from history: "
        + ", ".join(
            f"{entity_id} = {size} bytes ({size - MAX_ATTR_BYTES} over)"
            for entity_id, size in sorted(oversized.items())
        )
        + ". Full measurements: "
        + _report(sizes)
    )


async def test_the_overview_payload_does_not_grow_any_further(
    hass: HomeAssistant, stressed_entry: MockConfigEntry
) -> None:
    """A ratchet on the one sensor with essentially no room left.

    `sensor.choremander_overview` fits this household with a few hundred bytes to
    spare. That is not comfort, it is luck, and it is why the streak figures went
    onto their own entity rather than into `children[]` where they would have been
    convenient. Anything added here is the change that breaks a real family's
    history, so this holds the line separately from the softer headroom check.
    """
    size = _sensor_payloads(hass)["sensor.choremander_overview"]

    assert size <= OVERVIEW_RATCHET_BYTES, (
        f"sensor.choremander_overview has grown to {size} bytes, past the "
        f"{OVERVIEW_RATCHET_BYTES}-byte ratchet, and the recorder will silently "
        "stop storing its state. Put the new data on a companion entity - as "
        "ActivitySensor and StreaksSensor both do - instead of raising the ratchet."
    )


async def test_the_overview_payload_is_dominated_by_four_list_keys(
    hass: HomeAssistant, stressed_entry: MockConfigEntry
) -> None:
    """Name where the bytes actually go, so the fix is not a guessing game.

    Each of these four keys grows without bound with the household, and all four
    share a single entity's budget. Any of them would be a candidate to move onto
    a companion sensor; the measurement here says which would buy the most.
    """
    attributes = dict(hass.states.get("sensor.choremander_overview").attributes)
    key_sizes = {key: _encoded_size(value) for key, value in attributes.items()}
    biggest = sorted(key_sizes.items(), key=lambda item: -item[1])[:4]

    assert {key for key, _ in biggest} == {
        "todays_completions",
        "chores",
        "children",
        "rewards",
    }, (
        "the four unbounded list keys are expected to dominate the overview payload; "
        f"the actual top four were {biggest}"
    )
    assert sum(size for _, size in biggest) > 0.9 * sum(key_sizes.values()), (
        "over 90% of the overview payload is those four lists, so trimming scalar keys "
        f"will not help: {sorted(key_sizes.items(), key=lambda item: -item[1])}"
    )


async def test_the_activity_feed_limit_is_what_bounds_the_activity_sensor(
    hass: HomeAssistant, stressed_entry: MockConfigEntry
) -> None:
    """The feed is the one key that would otherwise grow with the ledger.

    `ACTIVITY_FEED_LIMIT` is the only thing between a long-lived install and an
    activity payload that grows for ever, so the cost of a single event has to be
    priced against the budget rather than guessed at. The ledger here holds three
    times the limit, which is what proves the cap - and not the data - is doing
    the bounding.
    """
    events = hass.states.get("sensor.choremander_activity").attributes["events"]
    assert len(events) == ACTIVITY_FEED_LIMIT, "the feed must be at its cap to price it"

    bytes_per_event = _encoded_size(events) / len(events)

    assert bytes_per_event < MAX_ATTR_BYTES / ACTIVITY_FEED_LIMIT, (
        f"each activity event costs {bytes_per_event:.0f} bytes, and "
        f"{ACTIVITY_FEED_LIMIT} of them would not fit inside the {MAX_ATTR_BYTES}-byte "
        f"budget even alone ({_encoded_size(events)} bytes for the feed). Either shrink "
        "the row - drop a key, shorten a value - or lower ACTIVITY_FEED_LIMIT; the two "
        "numbers have to be chosen together."
    )


# ---------------------------------------------------------------------------
# The early warning
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN DEBT (sensor.py, ChoremandorOverallStatsSensor.extra_state_attributes): "
        "sensor.choremander_overview measures 16213 bytes for 5 children / 25 chores / "
        "10 rewards - 99% of the recorder's 16384-byte ceiling, with 171 bytes to "
        "spare. It is inside the hard limit, so nothing is broken today, but it has no "
        "headroom at all: one more chore, or a sixth child, tips it over and that "
        "household's overview state stops being recorded. The cause is structural - "
        "`rewards[].calculated_costs` carries a per-child cost map on every reward, so "
        "the payload grows as children x rewards. Fixing it means splitting that map "
        "onto a companion entity and changing the shape the rewards card reads, which "
        "is deliberate work of its own. Remove this xfail once it is done."
    ),
)
async def test_every_sensor_keeps_a_comfortable_headroom_margin(
    hass: HomeAssistant, stressed_entry: MockConfigEntry
) -> None:
    """Warn at 75% of the budget, long before anything actually breaks.

    THE FIX WHEN THIS FAILS IS TO SPLIT THE PAYLOAD ONTO A COMPANION SENSOR, NOT
    TO RAISE THE THRESHOLD. 16384 is a database column limit inside the recorder,
    so the ceiling itself is not negotiable; the only lever is how much a single
    entity carries. `ActivitySensor` already exists for precisely this reason -
    the feed was split off the overview sensor rather than squeezed in beside it,
    and the next key that outgrows its home should go the same way.

    The remaining 25% is not slack to spend on new keys. It is the room a
    household bigger than this one needs, and it is the reason no user ever has
    to discover this limit on our behalf.
    """
    sizes = _sensor_payloads(hass)

    crowded = {entity_id: size for entity_id, size in sizes.items() if size > HEADROOM_BYTES}
    assert not crowded, (
        f"these sensors are over {HEADROOM_FRACTION:.0%} of the {MAX_ATTR_BYTES}-byte "
        f"recorder budget ({HEADROOM_BYTES} bytes) under the stress household: "
        + ", ".join(
            f"{entity_id} = {size} bytes ({size / MAX_ATTR_BYTES:.0%})"
            for entity_id, size in sorted(crowded.items())
        )
        + ". Nothing is broken yet, which is exactly the point of catching it here. "
        "Move the largest key onto a new companion sensor - as ActivitySensor already "
        "does for the feed - rather than relaxing this threshold, because the 16KiB "
        "ceiling is a recorder database limit and cannot be raised. Full measurements: "
        + _report(sizes)
    )


async def test_every_sensor_not_already_known_to_be_oversized_keeps_its_headroom(
    hass: HomeAssistant, stressed_entry: MockConfigEntry
) -> None:
    """The live half of the early warning, excluding only the known breach."""
    sizes = {
        entity_id: size
        for entity_id, size in _sensor_payloads(hass).items()
        if entity_id not in KNOWN_CROWDED
    }

    crowded = {entity_id: size for entity_id, size in sizes.items() if size > HEADROOM_BYTES}
    assert not crowded, (
        f"these sensors have crossed {HEADROOM_FRACTION:.0%} of the "
        f"{MAX_ATTR_BYTES}-byte recorder budget ({HEADROOM_BYTES} bytes): "
        + ", ".join(
            f"{entity_id} = {size} bytes ({size / MAX_ATTR_BYTES:.0%})"
            for entity_id, size in sorted(crowded.items())
        )
        + ". They still work, and this is the moment to act: split the largest key onto "
        "a companion entity rather than raising HEADROOM_FRACTION, because the 16KiB "
        "ceiling is a recorder database limit. Full measurements: " + _report(sizes)
    )


async def test_the_measured_sizes_are_reported_for_every_sensor(
    hass: HomeAssistant, stressed_entry: MockConfigEntry, capsys: pytest.CaptureFixture
) -> None:
    """Print the table, so `-s` on this file answers "how close are we?".

    A pass or fail alone never says whether the last change cost twenty bytes or
    two thousand. `pytest tests/unit/test_sensor_payload_size.py -s` prints every
    sensor's size and its share of the budget.
    """
    sizes = _sensor_payloads(hass)

    with capsys.disabled():
        print(f"\nChoremander sensor attribute sizes (recorder limit {MAX_ATTR_BYTES} bytes)")
        print(
            f"  household: {CHILD_COUNT} children, {CHORE_COUNT} chores, "
            f"{REWARD_COUNT} rewards, {LEDGER_ENTRIES} ledger entries"
        )
        for entity_id, size in sorted(sizes.items(), key=lambda item: -item[1]):
            flag = "  <-- OVER THE RECORDER LIMIT" if size >= MAX_ATTR_BYTES else ""
            print(f"  {size:>6} bytes  {size / MAX_ATTR_BYTES:>6.1%}  {entity_id}{flag}")

    assert all(size > 0 for size in sizes.values()), (
        "a sensor publishing zero bytes of attributes would make this whole guard vacuous"
    )
    assert len(sizes) == 4 + 2 * CHILD_COUNT, (
        "the household should yield overview, pending-approvals, activity and "
        f"streaks sensors plus two per child; got {sorted(sizes)}"
    )
