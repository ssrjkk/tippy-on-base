#!/usr/bin/env bash
# Backup with verification + offsite sync + alerting
# Usage: ./scripts/backup_and_monitor.sh [backup_dir]
# Cron: 0 */6 * * * /path/to/scripts/backup_and_monitor.sh >> /var/log/tippy-backup.log 2>&1
#
# Required env: DATABASE_URL, optional: BACKUP_REMOTE (rsync target), ALERT_WEBHOOK

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck source=./pg_env.sh
source "$SCRIPT_DIR/pg_env.sh"

if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

BACKUP_DIR="${1:-$PROJECT_ROOT/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/tipbot_${TIMESTAMP}.sql.gz"
RETENTION_DAYS=30
RETENTION_COUNT=14

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

if ! pg_path_probe pg_dump; then
    alert "pg_dump is not on PATH and no PostgreSQL install was found"
    exit 1
fi

if ! pg_dump -d "$DATABASE_URL" \
    --clean --if-exists --no-owner --no-privileges --schema=public \
    | gzip > "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    alert "Backup failed: pg_dump exited non-zero and no archive was kept"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "[$(date -Iseconds)] Backup created: $BACKUP_SIZE"

# The payload is what is verified, never the byte count or the gzip header: a
# `pg_dump` that dies mid-pipe leaves a valid, non-empty gzip of nothing, which
# restores "successfully" having restored no rows.
if ! pg_archive_is_real "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    alert "Backup failed: $BACKUP_FILE holds no PostgreSQL dump"
    exit 1
fi

USER_DATA_LINES=$(gunzip -c "$BACKUP_FILE" | { grep -c "^COPY public.users\|^INSERT INTO public.users"; } || true)
echo "[$(date -Iseconds)] Verification passed. users rows in the dump: ${USER_DATA_LINES:-0}"

if [ -n "${BACKUP_REMOTE:-}" ]; then
    echo "[$(date -Iseconds)] Syncing to remote: $BACKUP_REMOTE"
    rsync -az --delete "$BACKUP_DIR/" "$BACKUP_REMOTE" || alert "Remote sync failed"
fi

echo "[$(date -Iseconds)] Cleaning old backups (keep $RETENTION_COUNT files, max $RETENTION_DAYS days)..."
find "$BACKUP_DIR" -name "tipbot_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true
cd "$BACKUP_DIR"
ls -t tipbot_*.sql.gz 2>/dev/null | tail -n +$((RETENTION_COUNT + 1)) | xargs -r rm -- 2>/dev/null || true

echo "[$(date -Iseconds)] Backup complete: $BACKUP_FILE ($BACKUP_SIZE)"
