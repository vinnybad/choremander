# Testing Choremander

Everything runs locally against a real Home Assistant. There are three layers,
each catching things the others cannot.

| Layer | Location | Tests | Needs a container? | What it proves |
|---|---|---|---|---|
| **Unit** | `tests/unit/` | 347 | No | The Python logic is correct — pricing math, storage, config flow, entity state |
| **API end-to-end** | `tests/e2e/` | 13 | Yes | Real service calls against real Home Assistant move real state |
| **Browser end-to-end** | `e2e/` | 18 | Yes | The Lovelace cards actually work when a person clicks them |

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

## Known defects

The suite **pins current behaviour** rather than asserting what the code ought
to do, so it is green today and a future fix shows up as a deliberate,
test-visible change. Tests that pin a bug are marked:

```python
# CURRENT BEHAVIOUR (defect #2, see docs/TESTING.md)
```

| # | Defect |
|---|---|
| 1 | `frontend_registered` blocks service unregistration on the last unload, so `services_registered` stays `True` and a reload orphans all 8 services |
| 2 | `async_approve_chore` is not idempotent — approving twice pays out twice against one record |
| 3 | `async_reject_reward` recomputes the price at rejection time, so chore edits between claim and reject create or destroy points |
| 4 | `async_add_points` increments `total_chores_completed`, even for a zero-point adjustment |
| 5 | Unloading an entry twice raises `KeyError` |
| 6 | Rewards whose icon is not in `REWARD_ICON_OPTIONS` cannot be edited **or deleted** from the options UI — this affects 3 of the 5 seeded rewards |
| 7 | `async_setup_entry` overwrites options-flow point settings from `entry.data` on every restart |
| 8 | `set_chore_order` is registered but absent from `services.yaml`, so it is invisible in the Home Assistant UI |
| 9 | Chores and rewards added after setup never get buttons — the update listener body is `pass` |
| 10 | A claim button is created for every child × every reward, ignoring `assigned_to` |
| 11 | No server-side assignment check: any child can complete any chore and claim any reward |
| 12 | There is no `reject_reward` service, so a parent cannot deny a reward claim once made |
| 13 | A jackpot pools every child's points in its progress bar, but `claim_reward` charges one child, so a jackpot is only claimable once a single child holds the whole cost alone. The card gates on the rule the backend enforces rather than on the pooled total |

Three further defects were found and **fixed** while building this suite:

- **Rewards could not be claimed at all** ([issue #3](https://github.com/vinnybad/choremander/issues/3)).
  `_renderRewardRow` drew the reward icon as decoration — no button, no handler,
  no service call anywhere in the card. Every service and sensor behind it
  worked the whole time, which is why only the browser layer could catch it.

- The parent dashboard's approvals card was wired to `sensor.choremander_overview`,
  which has no `chore_completions` attribute, so it always showed "All caught
  up!" regardless of what was pending. Now points at `sensor.pending_approvals`.
- A fresh clone could not log in at all: onboarding was committed as complete
  while the auth store was gitignored, leaving Home Assistant convinced it was
  set up but with no users. `dev/bootstrap.py` now owns onboarding.

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
