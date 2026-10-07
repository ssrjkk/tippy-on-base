#!/usr/bin/env bash
# Database backup script for Tippy on Base
# Usage: ./scripts/backup_db.sh [backup_dir]
# Requires: pg_dump, DATABASE_URL or .env with DATABASE_URL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Ensure pg_dump is on PATH (Windows/PostgreSQL installs)
if ! command -v pg_dump &>/dev/null; then
    for d in "/c/Program Files/PostgreSQL"/*/bin; do
        [ -x "$d/pg_dump" ] && export PATH="$d:$PATH" && break
    done
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

# Dump database (exclude large binary tables if any)
pg_dump -d "$DATABASE_URL" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    --schema=public \
    | gzip > "$BACKUP_FILE"

# Verify backup
if [ ! -s "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file is empty"
    exit 1
fi

BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
echo "Backup completed: $BACKUP_FILE ($BACKUP_SIZE)"

# Keep only last 7 backups
cd "$BACKUP_DIR"
ls -t tipbot_*.sql.gz 2>/dev/null | tail -n +8 | xargs -r rm --
echo "Retention: kept last 7 backups"
