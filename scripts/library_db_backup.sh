#!/bin/bash
# Daily backup of ~/commonplace/library.db using SQLite's online .backup
# command (safe under concurrent worker writes — the SQLite VFS locks pages
# briefly rather than requiring a full quiesce). Compresses the output and
# rotates files older than RETENTION_DAYS.
#
# Invoked by launchd agent com.commonplace.library-backup at 03:00 daily.
# Can be run manually with no args to take an ad-hoc backup.
#
# Exit codes:
#   0  backup written and rotation completed
#   1  sqlite3 backup command failed
#   2  source DB missing or unreadable

set -euo pipefail

DB_PATH="${COMMONPLACE_DB_PATH:-$HOME/commonplace/library.db}"
BACKUP_DIR="${COMMONPLACE_BACKUP_DIR:-$HOME/commonplace/backups}"
RETENTION_DAYS="${COMMONPLACE_BACKUP_RETENTION_DAYS:-7}"

if [[ ! -r "$DB_PATH" ]]; then
    echo "ERROR: source DB missing or unreadable: $DB_PATH" >&2
    exit 2
fi

mkdir -p "$BACKUP_DIR"

STAMP=$(date +%Y-%m-%d_%H%M%S)
OUT="$BACKUP_DIR/library.db.${STAMP}.bak"

echo "[$(date -Iseconds)] backup: $DB_PATH -> $OUT"
sqlite3 "$DB_PATH" ".backup '$OUT'"
gzip -f "$OUT"

BYTES=$(stat -f%z "${OUT}.gz" 2>/dev/null || stat -c%s "${OUT}.gz")
echo "[$(date -Iseconds)] wrote ${OUT}.gz (${BYTES} bytes)"

# Rotate: delete .bak.gz files older than RETENTION_DAYS days.
# -mtime +N means strictly older than N days.
REMOVED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'library.db.*.bak.gz' -mtime "+${RETENTION_DAYS}" -print -delete 2>/dev/null | wc -l | tr -d ' ')
echo "[$(date -Iseconds)] rotated ${REMOVED} file(s) older than ${RETENTION_DAYS} days"

echo "[$(date -Iseconds)] current backups:"
ls -lh "$BACKUP_DIR" | grep 'library.db.*.bak.gz' || echo "  (none)"
