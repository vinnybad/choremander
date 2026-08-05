# Day-of-week chore scheduling

Design for [issue #9](https://github.com/vinnybad/choremander/issues/9): *"Is there
any way to assign chores by day as well? Especially during the weekends during the
school year and over the whole summer vacation we rotate certain chores by day, so
that the kids alternate or have 'bigger' chores only certain days."*

## Why build it

`due_days` already exists end to end — `Chore.due_days` (`models.py:108`), a
multi-select in all three chore forms (`config_flow.py:286, 391, 491`), a constant
(`const.py:23`), `DAYS_OF_WEEK` (`const.py:40-47`), storage round-trips and unit
tests. It is dropped at the sensor boundary: `chores_list` (`sensor.py:161-175`)
never emits it, so no card can see it and nothing filters on it.

A parent can set "Due Days: Monday, Friday" today, save, and the chore still appears
every day. That is a broken promise shipping in the product. Issue #9 asks us to
finish it.

The issue names two distinct patterns:

- **"'bigger' chores only certain days"** — a chore exists only on some weekdays.
  This is `due_days`, already modelled, merely unread.
- **"the kids alternate"** — who is on the chore changes by day.

Seasonal profiles (school year vs. summer) are **out of scope**. The issue describes
*when* the family rotates, not a request for named schedule sets. Parents re-edit
twice a year; revisit if it is actually asked for.

## Model

### `schedule.py` — a pure engine

New module with no Home Assistant imports, unit-testable in isolation, matching the
established pattern of `streaks.py` and `migration.py`.

```python
@dataclass
class ChoreSchedule:
    due_days: list[str]                     # [] means every day (back-compat)
    rotation: str = "none"                  # "none" | "occurrence" | "week"
    rotation_order: list[str] = []          # child IDs; [] falls back to assigned_to
    rotation_anchor: str = ""               # ISO date the cycle counts from
    day_overrides: dict[str, list[str]] = {}    # weekday  -> child IDs (permanent)
    date_overrides: dict[str, list[str]] = {}   # ISO date -> child IDs (one-off)
```

One function is the whole contract:

```python
assignees_on(schedule, assigned_to, day: date) -> list[str]
```

Resolution order, highest priority first:

| # | Layer | Meaning |
|---|-------|---------|
| 1 | `date_overrides[day]` | One-off. May also *add* an otherwise-unscheduled day. `[]` means explicitly nobody. |
| 2 | `day_overrides[weekday]` | Permanent weekday exception. |
| 3 | `due_days` miss | Not a due day → nobody. |
| 4 | `rotation == "none"` | → `assigned_to`. Exactly today's behaviour. |
| 5 | `rotation == "occurrence"` | Nth occurrence of the chore since anchor, mod `len(order)`. |
| 6 | `rotation == "week"` | Nth week since anchor, mod `len(order)`. |

Two cadences because "alternate" means two different things:

- **occurrence** — "the kids alternate each time." Dishes on Sat+Sun with
  `[Krishna, Maya]` gives Sat=Krishna, Sun=Maya, stable week to week.
- **week** — "Maya has kitchen duty this week." The whole week goes to one child.

A *daily* chore alternating between two children inverts every week (seven
occurrences, two children). That is what "alternate every day" literally means, not a
defect — and it is why the card renders **real dates** rather than an abstract
weekly template.

Resolution is a deterministic function of the date. Nothing is stored per day, so
there is no cursor to drift and no scheduled job to miss.

### Storage

All new fields default in `Chore.from_dict`, so **no storage version bump** — the
additive pattern already used for `daily_limit`, `completion_sound` and
`completion_percentage_per_month`. `date_overrides` are pruned past 60 days on write
so storage stays bounded.

## Companion sensor

The overview sensor measures **16,213 of 16,384 bytes** for the stress household —
171 bytes of headroom, guarded by a strict ratchet whose failure message reads: *"Put
the new data on a companion entity — as ActivitySensor and StreaksSensor both do —
instead of raising the ratchet"* (`test_sensor_payload_size.py:514-532`). Adding
anything per-chore to `chores_list` breaks it.

So `sensor.choremander_schedule` publishes the **resolved** schedule for the current
and next week, week-aligned:

```json
{
  "window_start": "2026-08-03",
  "window_days": 14,
  "days": {
    "2026-08-05": {"c1": ["k1"], "c2": ["k1", "m1"]},
    "2026-08-06": {"c1": ["m1"]}
  }
}
```

Resolved server-side means **no rotation arithmetic in JavaScript** — one
implementation, nothing to drift. Chores with no assignees on a day are omitted.

Two weeks is a deliberate bound, not an oversight: "every Saturday" edits need no
paging, and one-off swaps are almost always this week or next. It also keeps the new
payload comfortably inside the 75% headroom margin.

## Consumers

### Child card

Reads the companion entity via an optional `schedule_entity` config key defaulting to
`sensor.choremander_schedule`, and filters today's chores by whether the child
appears in that day's entry.

**If the entity is absent the card falls back to current behaviour** — an older card
config or a not-yet-loaded entity must never produce a broken card.

The empty state distinguishes two different feelings. Today it always says "All Done!
No chores right now. Great job!" (`choremander-child-card.js:1814-1822`). When the
child has nothing *scheduled*, it should say **"Day off 🎉 — next chores tomorrow"**;
"you finished everything" and "you had nothing to do" are not the same message.

### Streaks — days off must not break a streak

`compute_streaks` breaks a run on any calendar gap (`streaks.py:49-51, 54`). The
moment a chore is Mon–Sat, every child loses their streak every Sunday. *"I did
everything and still lost my streak"* destroys the motivation loop the product is
built on.

`compute_streaks` gains a `rest_days` set. A day on which the child had nothing
scheduled is **neutral**: it neither extends nor breaks the run. `StreaksSensor` (18%
of budget, ample room) publishes `rest_days` alongside `active_days`, and the streak
card's dot strip renders them as a distinct muted "day off" dot rather than a miss.

**Trade-off, stated plainly:** rest days are derived from the *current* schedule, so
changing a rule reshapes past streaks. This is consistent with the deliberate
"derive, never store" philosophy documented at the top of `streaks.py`; snapshotting
per day would reintroduce exactly the drift that module exists to prevent.

### Reward pricing — the silent trap

`calculate_dynamic_reward_costs` (`coordinator.py:367-372`) computes
`daily_expected = points × (completion_pct / 100)` and assumes every chore is
available every day. Ship scheduling without touching this and a parent who sets up a
Mon/Wed/Fri rotation gets reward costs inflated two- to four-fold. The child's goal
quietly becomes unreachable — a regression on the product's core promise.

One additional factor:

```
daily_expected = points × (scheduled_days_per_week / 7) × (completion_pct / 100)
```

An unscheduled chore is `7/7`, giving a number **identical to today's** — fully
backward compatible, and asserted as such in tests. Averaged over a 28-day window so
rotation cycles come out even.

This also lets `completion_percentage_per_month` mean one thing instead of two. Today
it conflates frequency ("50 = every other day") with reliability. The schedule now
owns frequency, so the field becomes purely *"when it is due, how often do they
actually do it?"* — and parents stop hand-computing a percentage the app can derive.
Form label and README wording change accordingly.

### Completion on an off day

`async_complete_chore` (`coordinator.py:508-539`) does not check the schedule and will
not start. Off-day chores are hidden from the card, so children cannot complete them
from the UI; the service stays permissive so automations and deliberate parent
overrides keep working. No new error path.

## Schedule card

`choremander-schedule-card.js`, following the `reorder-card` precedent exactly: read
the sensor, hold local edits, write back through a registered service.

```
┌─ Weekly Schedule ──────── ‹ Aug 3–9 › ──┐
│           M   T   W   T   F   S   S     │
│ Dishes    K   M   K   M   K   KM· ·     │
│ Trash     ·   ·   K   ·   ·   ·   ·     │
│ Bed       K   K   K   K   K   K   K     │
└─────────────────────────────────────────┘
   · marks a cell overridden off the rule
```

Clicking a cell opens who-is-on plus the scope choice — **"Just Aug 8"** vs **"Every
Saturday"** — the recurring-event pattern from every calendar app, so there is no new
concept to learn. Clicking a chore name edits its rotation rule. Overridden cells
carry a marker so the underlying rule stays legible.

New service `set_chore_schedule(chore_id, schedule)`. The missing `set_chore_order`
entry is added to `services.yaml` at the same time — it is registered in
`__init__.py` but undocumented, a pre-existing gap.

The config flow keeps only the existing `due_days` field. Seven dropdowns in a form
that already has ten fields is the wrong surface; the card is the editing surface.

## Testing

- **`tests/unit/test_schedule.py`** (new) — resolution order, both rotation cadences,
  override precedence, anchor arithmetic, empty/degenerate rotation orders, and date
  walking that does not assume 24-hour days.
- **`test_streaks.py`** — rest days are neutral; a run survives a scheduled day off;
  a genuine miss still breaks it.
- **`test_coordinator_pricing.py`** — scheduled chores price by their real frequency,
  and **unscheduled chores price identically to before** (explicit back-compat
  assertion).
- **`test_sensor_payload_size.py`** — the new sensor is measured and inside the
  headroom margin; the overview ratchet still holds.
- **`test_models.py` / `test_storage.py`** — schedule fields round-trip; absent fields
  default; overrides prune.
- **`tests/e2e/`** — a chore is visible on its due day and absent otherwise.
- **`e2e/specs/schedule-card.spec.ts`** — the suite already pins
  `timezoneId: "America/Los_Angeles"`, which this feature very much needs.

## Staging

1. **Engine and backend** — `schedule.py`, model fields, companion sensor, child-card
   filtering, streak rest days, pricing. At this point `due_days` works through the
   existing config flow, which alone solves *"bigger chores only certain days"*.
2. **Schedule card** — rotation and overrides become editable, solving *"the kids
   alternate"*.

## Open trade-offs

- **The two-week window** covers every realistic edit and keeps the payload small,
  but rules out setting an override a month out.
- **Retroactive streaks** (above) — the right call given the module's philosophy, but
  a real trade-off.
