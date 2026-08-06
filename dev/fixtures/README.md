# Dev environment fixtures

The canonical starting state for the local test environment: three children,
eleven chores, five rewards, and the dashboards that display them.

Three chores carry a day-of-week schedule, all of them Meera's or shared with
her, so that the scheduling feature is visible in the container without moving
any chore the browser specs expect to see every day.

`lovelace_resources` seeds one resource Choremander does not own,
`/local/third-party-card.js` (the file itself lives in `dev/config/www/`). It
stands in for the HACS-installed cards a real user has. Issue #2 — Choremander
truncating `.storage/lovelace_resources` to its own seven cards and erasing
everyone else's — survived precisely because this environment had no other
resources to lose. `tests/e2e/test_lovelace_resources.py` asserts it is still
there after startup and after a restart, so removing it would quietly make that
guard vacuous.

`dev/seed.sh` copies these files into `dev/config/.storage/`, overwriting
whatever the running container has accumulated. `dev/reset.sh` does the same as
part of a full wipe.

**These files, not `dev/config/.storage/`, are the fixture of record.** Home
Assistant rewrites everything under `.storage/` while it runs — point balances,
completions, chore order — so that directory reflects the last test run rather
than a known baseline. Restoring from here (instead of from git HEAD, which an
earlier version did) means a reset behaves identically whether or not the
working tree happens to be clean.

## Changing the fixture data

Edit the environment through the Home Assistant UI, confirm it is how you want
it, then copy the file back:

```bash
docker compose -f docker-compose.dev.yml stop homeassistant
cp dev/config/.storage/choremander.storage.choremander_dev_entry dev/fixtures/
docker compose -f docker-compose.dev.yml start homeassistant
```

Stop the container first — Home Assistant holds `.storage` in memory and
rewrites it on shutdown, which would discard the copy.

Both end-to-end suites reseed automatically, so any change here shifts the
numbers those tests assert. Expect to update them together.
