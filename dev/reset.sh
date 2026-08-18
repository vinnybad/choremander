#!/bin/bash
# Wipe all generated state, returning the environment to a fresh-clone baseline.
# The committed seed data under dev/config/.storage is preserved.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "Stopping any running containers..."
docker compose -f docker-compose.dev.yml down 2>/dev/null || true

# `stop` is synchronous, so nothing rewrites .storage after it returns - but its
# failure was being swallowed, and everything below would then be racing a live
# Home Assistant that rewrites the lot on its eventual shutdown. Confirm rather
# than assume.
if docker compose -f docker-compose.dev.yml ps --status running 2>/dev/null | grep -q homeassistant; then
  echo "Home Assistant is still running; refusing to touch dev/config underneath it." >&2
  echo "Stop it with './dev/stop.sh' and try again." >&2
  exit 1
fi

# The container wrote most of what is about to be deleted, and on Linux it
# wrote it as root. See dev/take-ownership.sh.
"$SCRIPT_DIR/take-ownership.sh"

echo "Removing generated files (keeping seeded config)..."
rm -rf dev/config/.storage/auth
rm -rf dev/config/.storage/auth_provider.homeassistant
rm -rf dev/config/.storage/onboarding
rm -rf dev/config/custom_components/hacs
rm -f dev/config/home-assistant_v2.db*
rm -f dev/config/home-assistant.log*
rm -f dev/.token

# Restore the fixture data Home Assistant rewrites while running, so a reset
# genuinely returns to a known baseline rather than the last run's leftovers.
# Sourced from dev/fixtures/, NOT from git, so this behaves identically whether
# or not the working tree is clean.
./dev/seed.sh

echo "Reset complete. Run ./dev/start.sh to set up fresh."
