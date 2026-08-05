# Testing Choremander

Everything runs locally against a real Home Assistant. There are three layers,
each catching things the others cannot.

| Layer | Location | Tests | Needs a container? | What it proves |
|---|---|---|---|---|
| **Unit** | `tests/unit/` | 502 | No | The Python logic is correct — pricing math, the points ledger, storage and its migration, streak arithmetic, config flow, entity state and payload size |
| **API end-to-end** | `tests/e2e/` | 24 | Yes | Real service calls against real Home Assistant move real state |
| **Browser end-to-end** | `e2e/` | 54 | Yes | The Lovelace cards actually work when a person clicks them |

Unit coverage is 97% of the integration, with `coordinator.py` — the dynamic
reward pricing — at 100%.

## Quick start

```bash
make install    # venv, Python deps, Node deps, Chromium
make up         # start Home Assistant, onboard, wait until Choremander loads
make verify     # lint + unit + API e2e + browser e2e
```

`make help` lists every target.

## The environment

`make up` runs `dev/start.sh`, which is idempotent and fully non-interactive:

1. `dev/bootstrap.py --pre-start` rewinds the onboarding marker when no auth
   store exists.
2. `docker compose` starts Home Assistant (pinned to **2026.2.3**, the same core
   version the Python harness tests against).
3. `dev/bootstrap.py` waits for HTTP, completes onboarding over the REST API,
   creates the `dev` user, captures tokens to `dev/.token`, and blocks until
   `sensor.choremander_overview` is available.

It fails loudly rather than leaving a half-started environment behind.

| URL | Contents |
|---|---|
| <http://localhost:8123/krishna> | Kid dashboard — chores, progress, rewards |
| <http://localhost:8123/radha> | Kid dashboard |
| <http://localhost:8123/parent> | Approvals, points management, chore reorder |

Login is `dev` / `dev`. `dev/.token` holds the credentials the tests use; it is
gitignored and regenerated on demand.

### Seeded fixture data

2 children (Krishna 25, Radha 15), 8 chores, 5 rewards.

Radha's dashboard carries three views that differ only in card configuration:
`/radha` at the default density, `/radha/compact` as a card-for-card mirror of
it with `density: compact`, and `/radha/layout` holding `cozy` plus a card with
every visibility toggle turned off. Keeping the first two identical apart from
that one key is what lets `card-layout.spec.ts` compare their geometry and
attribute the difference to the option rather than to the content.

**`dev/fixtures/` is the fixture of record**, not `dev/config/.storage/`. Home
Assistant rewrites everything under `.storage/` while it runs, so that directory
reflects the last test run rather than a known baseline. `dev/seed.sh` copies
`dev/fixtures/` over it; `dev/reset.sh` does the same as part of a full wipe.
See `dev/fixtures/README.md` to change the data.

Both end-to-end suites reseed automatically — the Python suite once per session,
the Playwright suite once per spec file. This is not optional hygiene: every
chore has `daily_limit: 1`, and a pending reward claim cannot be cancelled
through any public service (there is no `reject_reward`), so without a reset the
second run starts from wherever the first one stopped.

Set `CHOREMANDER_SKIP_SEED=1` to skip the reset while iterating on one test.

The unit suite does **not** read `dev/config/.storage/`. It uses
`tests/unit/dev_seed.py`, a frozen copy of the fixture, so it stays hermetic
whether or not a container is running. `tests/unit/test_fixtures_in_sync.py`
fails if the two ever drift.

## Writing tests

### Unit tests

Run in-process via `pytest-homeassistant-custom-component`; no container.

```bash
make test               # all of them
make cov                # with a coverage report
.venv/bin/python -m pytest tests/unit/test_coordinator_pricing.py -v
```

Fixtures live in `tests/unit/conftest.py`: `mock_config_entry`,
`init_integration`, `coordinator`.

Four of these files need no Home Assistant at all, which is the point of the
modules they cover being free of HA imports:

| File | Covers | Why it is standalone |
|---|---|---|
| `test_streaks.py` | `streaks.py` | Streak arithmetic takes a set of dates and a `today`, so gaps, month and year boundaries, leap days and the yesterday grace period are all table-driven rather than staged against a live clock |
| `test_migration.py` | `migration.py` | A user's storage file cannot be recreated if the migration is wrong, so the malformed-input cases matter as much as the happy path |
| `test_activity_feed.py` | `activity.py` | Feed shape and ordering, including across daylight-saving transitions |
| `test_points_ledger.py` | the ledger paths in `coordinator.py` | Needs the coordinator, so this one does use the container-free HA harness |

`test_sensor_payload_size.py` is a different kind of guard: it builds a
deliberately oversized household and measures every sensor's encoded attribute
size against the recorder's 16KiB ceiling. **When it fails, the fix is to move
data onto a companion entity, never to raise the threshold** — the limit is a
recorder database column, and `ActivitySensor` and `StreaksSensor` both exist
for exactly this reason.

`test_readme_card_examples.py` guards the documentation rather than the code: it
reads every copyable YAML block in the README and asserts each one names the
entity that card's `getStubConfig()` declares. A card handed the wrong sensor
finds none of the attributes it reads and renders its empty state, so a wrong
example looks like a working card that has nothing to show (defect below).

`bypass_frontend_registration` is **opt-in, not autouse**, and should stay that
way. `frontend.py` writes `frontend_registered` into the same `hass.data[DOMAIN]`
dict that `__init__.py` scans on unload, so bypassing registration changes
teardown behaviour. Making it automatic hid a real bug (defect 1 below).

Network access is blocked by `pytest-socket`. That is intentional.

### API end-to-end tests

`tests/e2e/` drives the live container over REST through `HAClient`.

```bash
make e2e
```

Because service calls return before the coordinator has pushed new state, assert
with `ha.wait_for(...)` rather than reading straight back.

The suite reseeds once per session, but `tests/e2e/test_ledger_and_streaks.py`
reseeds again for its own module. Chores carry a daily limit, so a chore another
module already completed today cannot be completed again, and its reconciliation
test needs the ledger to start empty against known balances.

### Browser end-to-end tests

`e2e/` drives the real cards with Playwright.

```bash
make browser          # headless
make browser-headed   # watch it happen
```

`e2e/global-setup.ts` seeds Home Assistant's `hassTokens` localStorage key from
`dev/.token`, so specs never touch the login form.

All selectors live in `e2e/pages.ts`. The cards render into **open** shadow
roots, so Playwright's CSS engine descends into them automatically — no `>>>`,
no manual `shadowRoot` walking. Never use the `>` combinator across a host
boundary.

Two traps worth knowing:

- **Every chore has `daily_limit: 1`.** Two tests that complete the same chore
  will conflict: the second one undoes it. Each mutating spec owns a distinct
  (child, chore) pair — the allocation is documented at the top of each file.
- **A successful completion covers the viewport** with a celebration overlay and
  confetti for up to 3.5s. `ChildCard.complete()` and `RewardsCard.claim()` wait
  it out; a raw `.click()` followed by another interaction will hit the overlay
  instead.
- **`recordServiceCalls` must be attached before `openDashboard`.** It listens
  for websockets the page opens *after* it is installed, and the frontend opens
  its socket during load. Attach it late and every assertion sees zero traffic —
  which reads as "the card never called the service" rather than as a bug in the
  test.
- **Nothing is affordable at the seeded balances** (Krishna 25, Radha 15, and
  the cheapest reward is 50). A spec that exercises claiming has to call
  `grantPoints()` first. `e2e/specs/rewards-card.spec.ts` documents the running
  balance each of its tests assumes.
- **`rem` resolves against 14px, not 16px.** Home Assistant sets its own root
  font size, so a rule written as `1.7rem` computes to `23.8px`. Any assertion
  on a computed font size has to use that scale.
- **Playwright's default viewport is 1280x720**, which lands inside the child
  card's `(min-width: 1024px)` block *and* its `(max-height: 600px)` block is
  just missed. A spec asserting on sizes should set its own viewport rather
  than inherit one and silently test a different breakpoint than it reads like.
- **`hasText` matches substrings.** A view holding both "Streaks" and "Compact
  Streaks" has the first name select both cards, and the failure reads as a
  duplicated element rather than as a bad selector. `ActivityCard.byTitle` and
  `StreakCard.byTitle` anchor the match with a regex; use them rather than
  filtering by title yourself.
- **The activity and streak specs stage their own history.** The committed
  fixture carries no completions, so every row they assert on is produced by
  really calling the service that produces it. That is deliberate: it exercises
  the ledger, the migration, the sensor and the card as one chain. Multi-day
  streaks cannot be staged against a live clock and live in `test_streaks.py`
  instead.

## Known defects

The suite **pins current behaviour** rather than asserting what the code ought
to do, so it is green today and a future fix shows up as a deliberate,
test-visible change. Tests that pin a bug are marked:

```python
# CURRENT BEHAVIOUR (defect #3, see docs/TESTING.md)
```

| # | Defect |
|---|---|
| 1 | `frontend_registered` blocks service unregistration on the last unload, so `services_registered` stays `True` and a reload orphans all 8 services |
| 3 | `async_reject_reward` recomputes the price at rejection time, so chore edits between claim and reject create or destroy points |
| 5 | Unloading an entry twice raises `KeyError` |
| 6 | Rewards whose icon is not in `REWARD_ICON_OPTIONS` cannot be edited **or deleted** from the options UI — this affects 3 of the 5 seeded rewards |
| 7 | `async_setup_entry` overwrites options-flow point settings from `entry.data` on every restart |
| 8 | `set_chore_order` is registered but absent from `services.yaml`, so it is invisible in the Home Assistant UI |
| 9 | Chores and rewards added after setup never get buttons — the update listener body is `pass` |
| 10 | A claim button is created for every child × every reward, ignoring `assigned_to` |
| 11 | No server-side assignment check: any child can complete any chore and claim any reward |
| 12 | There is no `reject_reward` service, so a parent cannot deny a reward claim once made |
| 13 | A jackpot pools every child's points in its progress bar, but `claim_reward` charges one child, so a jackpot is only claimable once a single child holds the whole cost alone. The card gates on the rule the backend enforces rather than on the pooled total |
| 14 | The child card's breakpoints read the **viewport** width, so a card in a narrow masonry column on a wide screen gets the large-screen sizes and its chore names wrap to three lines. The `min-width: 1024px` block makes the card *bigger*, which is most of ["huge on a tablet"](https://github.com/vinnybad/choremander/issues/4). Fixing it properly means container queries, which would change how every existing dashboard renders, so `density` sidesteps it instead: the presets set one size that overrides every breakpoint at once |
| 15 | `sensor.choremander_overview` measures ~99% of the recorder's 16KiB attribute limit for a household of five children, past which Home Assistant silently stops recording the entity. The cause is structural: `rewards[].calculated_costs` carries a per-child cost map on every reward, so the payload grows as children × rewards. `tests/unit/test_sensor_payload_size.py` measures it, ratchets it so an unrelated change cannot be the one that tips it over, and holds every other sensor to a 75% margin. Fixing it means changing the shape the rewards card reads. This is why the activity feed and the streak figures each went onto their own entity rather than into the overview |
| 16 | Chore completions are never pruned. Streaks are derived from them on every coordinator refresh, so the per-refresh cost grows with history - measured at roughly 190ms for 50,000 completions, about three and a half years for a busy four-child family. Most of that is deserialising the completions, which the coordinator already did before streaks existed; the derivation itself is under 1ms. A retention window would fix both |

Defects **fixed** since this suite was built:

- **Rewards could not be claimed at all** ([issue #3](https://github.com/vinnybad/choremander/issues/3)).
  `_renderRewardRow` drew the reward icon as decoration — no button, no handler,
  no service call anywhere in the card. Every service and sensor behind it
  worked the whole time, which is why only the browser layer could catch it.

- The parent dashboard's approvals card was wired to `sensor.choremander_overview`,
  which has no `chore_completions` attribute, so it always showed "All caught
  up!" regardless of what was pending. Now points at `sensor.pending_approvals`.
  The README's example carried the same wrong entity long after the dashboard
  fixture was corrected, which is what anyone copying YAML actually hit
  ([issue #5](https://github.com/vinnybad/choremander/issues/5)).
  `tests/unit/test_readme_card_examples.py` now holds every documented example
  to the entity its card's `getStubConfig()` names, so the two cannot drift
  apart again.
- A fresh clone could not log in at all: onboarding was committed as complete
  while the auth store was gitignored, leaving Home Assistant convinced it was
  set up but with no users. `dev/bootstrap.py` now owns onboarding.
- **Streaks could only ever read zero** (was defect #15). `current_streak` and
  `best_streak` were stored on every child and published by two sensors, and no
  code path anywhere wrote them. They are now derived from the approved
  completion history on each coordinator refresh (`streaks.py`), so there is no
  mutable counter left to drift.
- **`async_approve_chore` was not idempotent** (was defect #2). Approving twice
  paid out twice against a single completion record; two parents tapping the
  same notification was enough. It now returns early if the completion is
  already approved.
- **A manual bonus counted as a chore** (was defect #4). `async_add_points`
  routed through `_award_points`, which incremented `total_chores_completed`
  unconditionally. That counter now drives the streak card's milestones, so it
  has to mean what it says.
- **The reason for a points adjustment was discarded.** `add_points` and
  `remove_points` accepted a `reason` and dropped it, leaving every manual
  adjustment as an unexplained movement. It is now recorded in the points
  ledger and shown on the activity card.
- **`make validate` broke permanently the first time anyone ran `make up`.**
  Starting the dev container leaves an empty
  `dev/config/custom_components/choremander/` behind as its bind-mount point.
  hassfest walks the whole mounted workspace, read that as an integration with
  no manifest, and died on a `KeyError`. The target now exports only the files
  git tracks, which is the tree CI checks out anyway.
- **Reward pricing logged at WARNING on every refresh.** `CALC_COSTS:` lines,
  three per call plus one per priced child, flooded the log of every install.
  Demoted to DEBUG.

## Continuous integration

`.github/workflows/test.yml` runs lint and unit tests on every push, then brings
up the container and runs both end-to-end suites. `hassfest` and HACS validation
run in their own existing workflows; `make validate` runs hassfest locally.

## Upgrading Home Assistant

The container image in `docker-compose.dev.yml` and the pin in
`requirements-test.txt` must move together — the cards bootstrap LitElement off
Home Assistant's own internals, so an unpinned bump can break all six at once.

```
pytest-homeassistant-custom-component==0.13.316  ->  homeassistant 2026.2.3
```

Check which core version a given harness release pins before changing either.
