#!/bin/bash
# Bring the full dev environment up and leave it ready to drive.
#
# Idempotent: safe to re-run against an already-running container.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python3}"

cd "$PROJECT_DIR"

# Download HACS if not present
if [ ! -d "dev/config/custom_components/hacs" ]; then
  echo "First run - running setup..."
  ./dev/setup.sh
fi

# On a fresh clone the working copies under dev/config/.storage/ do not exist -
# they are generated from dev/fixtures/, which is the fixture of record.
if [ ! -f "dev/config/.storage/choremander.storage.choremander_dev_entry" ]; then
  echo "No seed data found - installing it from dev/fixtures/..."
  ./dev/seed.sh
fi

# Offline fixups that must happen while the container is stopped, because Home
# Assistant keeps .storage in memory and rewrites it on shutdown.
"$PYTHON" dev/bootstrap.py --pre-start

echo "Starting Choremander dev environment..."
docker compose -f docker-compose.dev.yml up -d

# Wait for HA to answer, create the dev user if needed, and confirm Choremander
# actually loaded. Fails loudly rather than leaving a half-up environment.
"$PYTHON" dev/bootstrap.py

cat <<'EOF'

Home Assistant ready at http://localhost:8123
Login: dev / dev
Credentials for automated tests: dev/.token

Dashboards:
  http://localhost:8123/krishna  (kid)
  http://localhost:8123/radha    (kid)
  http://localhost:8123/parent   (approvals, points, reorder)

Commands:
  ./dev/stop.sh     - Stop the container
  ./dev/restart.sh  - Restart to pick up Python changes
  ./dev/logs.sh     - View filtered logs
  ./dev/reset.sh    - Wipe generated state and start over
  make verify       - Run the whole test suite against this environment
EOF
