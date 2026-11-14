#!/usr/bin/env bash
# Database backup script for Tippy on Base
# Usage: ./scripts/backup_db.sh [backup_dir]
# Requires: pg_dump, DATABASE_URL or .env with DATABASE_URL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck source=./pg_env.sh
source "$SCRIPT_DIR/pg_env.sh"

# Ensure pg_dump is on PATH (Windows/PostgreSQL installs)
if ! pg_path_probe pg_dump; then
    echo "ERROR: pg_dump is not on PATH and no PostgreSQL install was found" >&2
    exit 1
fi

# Load .env if present
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

BACKUP_DIR="${1:-$PROJECT_ROOT/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/tipbot_${TIMESTAMP}.sql.gz"

# Validate DATABASE_URL
if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "Backing up database to $BACKUP_FILE..."

# Dump database (exclude large binary tables if any). The failure of pg_dump
# inside a pipe used to abort the script and leave the archive it had already
# opened on disk — a 20-byte gzip of nothing that every later check accepted.
if ! pg_dump -d "$DATABASE_URL" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --schema=public \
    | gzip > "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    echo "ERROR: pg_dump failed; no backup written" >&2
    exit 1
fi

# Verify the payload, not the file size.
if ! pg_archive_is_real "$BACKUP_FILE"; then
    rm -f "$BACKUP_FILE"
    echo "ERROR: $BACKUP_FILE holds no PostgreSQL dump; discarded" >&2
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "Backup completed: $BACKUP_FILE ($BACKUP_SIZE)"

# Keep only last 7 backups
cd "$BACKUP_DIR"
ls -t tipbot_*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm --
echo "Retention: kept last 7 backups"
