#!/bin/bash
# Restore Choremander's data and dashboards to the committed fixture.
#
# The end-to-end suites mutate real persisted state, and some of it cannot be
# undone through the public services (there is no reject_reward, so a pending
# reward claim is one-way). Restoring from dev/fixtures/ is what makes repeated
# runs deterministic.
#
# Fixtures are copied from dev/fixtures/ rather than checked out from git, so a
# reset behaves the same whether or not the working tree is clean.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python3}"
FIXTURES="$SCRIPT_DIR/fixtures"
STORAGE="$SCRIPT_DIR/config/.storage"

cd "$PROJECT_DIR"

if [ ! -d "$FIXTURES" ]; then
  echo "Missing $FIXTURES - cannot restore a known state." >&2
  exit 1
fi

# Home Assistant holds .storage in memory and rewrites it on shutdown, so it has
# to be stopped before the files are replaced or it would overwrite us.
WAS_RUNNING=0
if docker compose -f docker-compose.dev.yml ps --status running 2>/dev/null | grep -q homeassistant; then
  WAS_RUNNING=1
fi

docker compose -f docker-compose.dev.yml stop homeassistant >/dev/null 2>&1 || true

# On Linux the files Home Assistant just rewrote on shutdown belong to root, and
# the copy below would fail on them. See dev/take-ownership.sh.
"$SCRIPT_DIR/take-ownership.sh"

mkdir -p "$STORAGE"
for fixture in "$FIXTURES"/*; do
  name="$(basename "$fixture")"
  [ "$name" = "README.md" ] && continue
  cp "$fixture" "$STORAGE/$name"
done

if [ "$WAS_RUNNING" = "1" ] || [ "${FORCE_START:-0}" = "1" ]; then
  docker compose -f docker-compose.dev.yml start homeassistant >/dev/null
  "$PYTHON" dev/bootstrap.py
fi

echo "Seed data restored."
