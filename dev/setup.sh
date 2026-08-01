#!/bin/bash
# One-time preparation of the dev config directory.
#
# HACS is only a distribution mechanism - Choremander itself does not need it,
# and fetching it adds a network dependency plus startup time to every fresh
# environment. It is therefore opt-in: set WITH_HACS=1 to install it when you
# specifically want to exercise the HACS install path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$SCRIPT_DIR/config"
WITH_HACS="${WITH_HACS:-0}"

echo "Setting up Choremander dev environment..."

mkdir -p "$CONFIG_DIR/.storage"

if [ "$WITH_HACS" = "1" ] && [ ! -d "$CONFIG_DIR/custom_components/hacs" ]; then
  echo "Downloading HACS..."
  mkdir -p "$CONFIG_DIR/custom_components"
  # curl rather than wget: macOS ships curl but not wget.
  curl -fsSL -o "$CONFIG_DIR/hacs.zip" \
    "https://github.com/hacs/integration/releases/latest/download/hacs.zip"
  unzip -q -o "$CONFIG_DIR/hacs.zip" -d "$CONFIG_DIR/custom_components/hacs"
  rm -f "$CONFIG_DIR/hacs.zip"
  echo "HACS downloaded."
fi

echo "Setup complete. The dev user is created by dev/bootstrap.py on first start."
