#!/usr/bin/env bash
# Panic button. Stops services, snapshots the database and vault, drops to a clean shell.
# Idempotent. Safe to run even if nothing is loaded.

set -euo pipefail

echo "Entering safe mode..."

# Stop services (tolerate not-loaded)
launchctl unload ~/Library/LaunchAgents/com.commonplace.server.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.commonplace.worker.plist 2>/dev/null || true

# Snapshot
SNAPSHOT_DIR=~/commonplace/snapshots/safe-mode-$(date +%Y%m%d-%H%M%S)
mkdir -p "$SNAPSHOT_DIR"

if [ -f ~/commonplace/library.db ]; then
  cp ~/commonplace/library.db "$SNAPSHOT_DIR/"
fi

if [ -d ~/commonplace ]; then
  tar -czf "$SNAPSHOT_DIR/vault.tar.gz" \
    -C ~/commonplace \
    --exclude='*.db' \
    --exclude='snapshots' \
    --exclude='.git' \
    . 2>/dev/null || true
fi

echo "Snapshot saved to $SNAPSHOT_DIR"
echo "Services stopped. You're in a safe shell."
echo "To restart: launchctl load ~/Library/LaunchAgents/com.commonplace.server.plist"
exec "$SHELL"
