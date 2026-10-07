#!/usr/bin/env bash
# Backup with verification + offsite sync + alerting
# Usage: ./scripts/backup_and_monitor.sh [backup_dir]
# Cron: 0 */6 * * * /path/to/scripts/backup_and_monitor.sh >> /var/log/tippy-backup.log 2>&1
#
# Required env: DATABASE_URL, optional: BACKUP_REMOTE (rsync target), ALERT_WEBHOOK

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

BACKUP_DIR="${1:-$PROJECT_ROOT/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/tipbot_${TIMESTAMP}.sql.gz"
VERIFY_FILE="$BACKUP_DIR/tipbot_${TIMESTAMP}.verify"
RETENTION_DAYS=30
RETENTION_COUNT=14

if ! command -v pg_dump &>/dev/null; then
    for d in "/c/Program Files/PostgreSQL"/*/bin; do
        [ -x "$d/pg_dump" ] && export PATH="$d:$PATH" && break
    done
fi

if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

alert() {
    local msg="$1"
    echo "ALERT: $msg"
    if [ -n "${ALERT_WEBHOOK:-}" ]; then
        curl -sf -X POST "$ALERT_WEBHOOK" \
            -H "Content-Type: application/json" \
            -d "{\"text\":\"[tippy-backup] $msg\"}" || true
    fi
}

echo "[$(date -Iseconds)] Starting backup to $BACKUP_FILE..."

pg_dump -d "$DATABASE_URL" \
    --clean --if-exists --no-owner --no-privileges --schema=public \
    | gzip > "$BACKUP_FILE"

if [ ! -s "$BACKUP_FILE" ]; then
    alert "Backup failed: empty file"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date -Iseconds)] Backup created: $BACKUP_SIZE"

echo "[$(date -Iseconds)] Verifying backup integrity..."
gzip -t "$BACKUP_FILE" 2>"$VERIFY_FILE"
if [ $? -ne 0 ]; then
    alert "Backup verification FAILED: $BACKUP_FILE is corrupt"
    rm -f "$VERIFY_FILE"
    exit 1
fi
rm -f "$VERIFY_FILE"

ROW_COUNT=$(pg_dump -d "$DATABASE_URL" --table=users --data-only --quotes 2>/dev/null | grep -c "INSERT\|COPY" || echo "0")
echo "[$(date -Iseconds)] Verification passed. Users table rows: ~$ROW_COUNT"

if [ -n "${BACKUP_REMOTE:-}" ]; then
    echo "[$(date -Iseconds)] Syncing to remote: $BACKUP_REMOTE"
    rsync -az --delete "$BACKUP_DIR/" "$BACKUP_REMOTE" || alert "Remote sync failed"
fi

echo "[$(date -Iseconds)] Cleaning old backups (keep $RETENTION_COUNT files, max $RETENTION_DAYS days)..."
find "$BACKUP_DIR" -name "tipbot_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
cd "$BACKUP_DIR"
ls -t tipbot_*.sql.gz 2>/dev/null | tail -n +$((RETENTION_COUNT + 1)) | xargs -r rm -- 2>/dev/null || true

echo "[$(date -Iseconds)] Backup complete: $BACKUP_FILE ($BACKUP_SIZE)"
