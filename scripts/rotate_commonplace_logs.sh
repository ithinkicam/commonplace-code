#!/bin/bash
# Rotate commonplace-* launchd logs under ~/Library/Logs/.
#
# launchd does not rotate the files pointed to by StandardOutPath /
# StandardErrorPath — they grow forever. This script is invoked daily by
# com.commonplace.log-rotate.plist and does a truncate-and-copy rotation
# so readers (tail, open logs) keep working without re-opening.
#
# Retention: keep the last COMMONPLACE_LOG_KEEP historical copies
# (default 3) per log file; anything older is deleted. A log file
# smaller than COMMONPLACE_LOG_MIN_BYTES (default 1 MB) is skipped so we
# don't churn trivial amounts of data.
#
# Exit codes:
#   0  success (including nothing-to-rotate)
#   1  log directory not found
#
# Env overrides:
#   COMMONPLACE_LOG_DIR        default ~/Library/Logs
#   COMMONPLACE_LOG_PATTERN    default 'commonplace-*.log'
#   COMMONPLACE_LOG_MIN_BYTES  default 1048576 (1 MB — below this, skip)
#   COMMONPLACE_LOG_KEEP       default 3

set -euo pipefail

LOG_DIR="${COMMONPLACE_LOG_DIR:-$HOME/Library/Logs}"
PATTERN="${COMMONPLACE_LOG_PATTERN:-commonplace-*.log}"
MIN_BYTES="${COMMONPLACE_LOG_MIN_BYTES:-1048576}"
KEEP="${COMMONPLACE_LOG_KEEP:-3}"

if [[ ! -d "$LOG_DIR" ]]; then
    echo "ERROR: log dir not found: $LOG_DIR" >&2
    exit 1
fi

shopt -s nullglob
STAMP=$(date +%Y%m%d_%H%M%S)

for log in "$LOG_DIR"/$PATTERN; do
    [[ -f "$log" ]] || continue

    BYTES=$(stat -f%z "$log" 2>/dev/null || stat -c%s "$log")
    if (( BYTES < MIN_BYTES )); then
        continue
    fi

    rotated="${log}.${STAMP}"
    # Copy-then-truncate keeps the file descriptor held by the producing
    # process valid. Atomic rename would orphan the producer's handle and
    # send subsequent writes into an unlinked inode that disappears on
    # process exit.
    cp -p "$log" "$rotated"
    gzip -f "$rotated"
    : > "$log"

    echo "[$(date -Iseconds)] rotated $log -> ${rotated}.gz ($BYTES bytes)"

    # Prune old rotations for this base name, keeping KEEP most recent.
    base=$(basename "$log")
    ls -t "$LOG_DIR/${base}."*.gz 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
        rm -f "$old"
        echo "[$(date -Iseconds)] pruned $old"
    done
done
