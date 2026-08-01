#!/bin/bash
# Restart Home Assistant to pick up Python changes, then block until it is
# actually serving again so callers can proceed immediately.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python3}"

cd "$PROJECT_DIR"

echo "Restarting Home Assistant to pick up code changes..."
docker compose -f docker-compose.dev.yml restart homeassistant

"$PYTHON" dev/bootstrap.py
