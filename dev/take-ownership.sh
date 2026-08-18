#!/bin/bash
# Hand dev/config back to whoever is running these scripts.
#
# Home Assistant runs as root inside the container, so every file it writes to
# the bind-mounted dev/config - all of .storage, the database, the logs - is
# owned by root. On Linux that ownership is the host's ownership, so seed.sh
# cannot replace those files and reset.sh cannot delete them. Docker Desktop on
# macOS remaps container writes to the host user, which is why this only ever
# bites on Linux, and why CI was the first place it showed up.
#
# The reclaim runs inside a throwaway container that is already root, so nothing
# here needs sudo - which a GitHub runner has but a locked-down workstation may
# not. Safe to call whenever; it is a no-op once the tree is already ours.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG="$SCRIPT_DIR/config"

[ -d "$CONFIG" ] || exit 0

# Fast path: skip the container entirely when nothing in the tree is foreign.
# This is the normal case on macOS, and on Linux before the container's first
# run, so the common path stays as cheap as it was before.
#
# A `find` that cannot walk the tree is itself a symptom of the ownership this
# script exists to fix, so its failure falls through to the reclaim rather than
# being read as "nothing to do".
if foreign="$(find "$CONFIG" ! -user "$(id -u)" -print -quit 2>/dev/null)" && [ -z "$foreign" ]; then
  exit 0
fi

# Reuse the pinned Home Assistant image rather than naming a second one, so this
# never drifts from docker-compose.dev.yml and never costs an extra pull.
IMAGE="$(docker compose -f "$PROJECT_DIR/docker-compose.dev.yml" config --images | head -n 1)"

echo "Reclaiming dev/config from the container's root user..."
docker run --rm \
  -v "$CONFIG":/config \
  --entrypoint chown \
  "$IMAGE" -R "$(id -u):$(id -g)" /config
