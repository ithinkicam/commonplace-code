#!/bin/bash
# Restore ~/commonplace/library.db from a .bak.gz produced by
# scripts/library_db_backup.sh.
#
# Safety:
#   - Verifies the gzip is readable (gzip -t) before touching anything.
#   - Decompresses into a scratch dir, not over the live DB.
#   - Runs `PRAGMA integrity_check` on the decompressed image; aborts on
#     anything other than "ok".
#   - Snapshots the current DB (if present) to ${DB_PATH}.pre_restore.<stamp>
#     before swapping, so a mistaken restore is one `mv` away from reversal.
#   - Does *not* modify the original .bak.gz.
#
# Usage:
#   scripts/library_db_restore.sh                     # restore latest backup
#   scripts/library_db_restore.sh <path/to/*.bak.gz>  # restore a specific file
#
# Env:
#   COMMONPLACE_DB_PATH     default ~/commonplace/library.db
#   COMMONPLACE_BACKUP_DIR  default ~/commonplace/backups
#
# Exit codes:
#   0  restore completed
#   1  verification failed (gzip corrupt, integrity_check failed, etc.)
#   2  no backup found / bad argument
#   3  worker/server still running — refuse to swap a DB that's in use

set -euo pipefail

DB_PATH="${COMMONPLACE_DB_PATH:-$HOME/commonplace/library.db}"
BACKUP_DIR="${COMMONPLACE_BACKUP_DIR:-$HOME/commonplace/backups}"

if [[ $# -gt 0 ]]; then
    SRC="$1"
    if [[ ! -r "$SRC" ]]; then
        echo "ERROR: not readable: $SRC" >&2
        exit 2
    fi
else
    # Pick the newest library.db.*.bak.gz in BACKUP_DIR.
    SRC=$(ls -1t "$BACKUP_DIR"/library.db.*.bak.gz 2>/dev/null | head -n1 || true)
    if [[ -z "$SRC" ]]; then
        echo "ERROR: no backups found in $BACKUP_DIR" >&2
        exit 2
    fi
fi

echo "[$(date -Iseconds)] source backup: $SRC"

# Refuse to restore over a DB that services are actively using. Checking for
# the process names is best-effort — if pgrep isn't installed we just skip.
if command -v pgrep >/dev/null 2>&1; then
    if pgrep -f "commonplace_worker" >/dev/null || pgrep -f "commonplace_server" >/dev/null; then
        echo "ERROR: commonplace worker/server appears to be running; stop it first" >&2
        echo "       (e.g. launchctl bootout gui/\$(id -u)/com.commonplace.worker)" >&2
        exit 3
    fi
fi

# Verify the gzip archive itself before we start changing state.
if ! gzip -t "$SRC" 2>/dev/null; then
    echo "ERROR: gzip verification failed for $SRC" >&2
    exit 1
fi

SCRATCH=$(mktemp -d -t commonplace_restore.XXXXXX)
trap 'rm -rf "$SCRATCH"' EXIT

STAGING="$SCRATCH/library.db.staging"
gunzip -c "$SRC" > "$STAGING"

# Integrity check on the decompressed image before trusting it.
INTEGRITY=$(sqlite3 "$STAGING" "PRAGMA integrity_check;" 2>&1)
if [[ "$INTEGRITY" != "ok" ]]; then
    echo "ERROR: integrity_check on decompressed backup failed: $INTEGRITY" >&2
    exit 1
fi
echo "[$(date -Iseconds)] integrity_check=ok"

# Snapshot the current DB (and its WAL/SHM siblings if present) so this
# restore is reversible.
if [[ -f "$DB_PATH" ]]; then
    STAMP=$(date +%Y-%m-%d_%H%M%S)
    PRE="${DB_PATH}.pre_restore.${STAMP}"
    cp -p "$DB_PATH" "$PRE"
    [[ -f "${DB_PATH}-wal" ]] && cp -p "${DB_PATH}-wal" "${PRE}-wal" || true
    [[ -f "${DB_PATH}-shm" ]] && cp -p "${DB_PATH}-shm" "${PRE}-shm" || true
    echo "[$(date -Iseconds)] snapshot of current DB: $PRE"
fi

# Swap in the restored DB. Rename is atomic on the same filesystem.
mkdir -p "$(dirname "$DB_PATH")"
mv "$STAGING" "$DB_PATH"

# A WAL file from a previous session would now describe pages that don't
# exist in the restored image; remove it so SQLite opens a fresh journal.
rm -f "${DB_PATH}-wal" "${DB_PATH}-shm"

echo "[$(date -Iseconds)] restored $DB_PATH from $SRC"
echo "[$(date -Iseconds)] to roll back: mv ${DB_PATH}.pre_restore.* back into place"
