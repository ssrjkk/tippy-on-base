#!/usr/bin/env bash
# Database restore script for Tippy on Base
# Usage: ./scripts/restore_db.sh <backup_file>
# Requires: psql, DATABASE_URL or .env with DATABASE_URL

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# shellcheck source=./pg_env.sh
source "$SCRIPT_DIR/pg_env.sh"

# Load .env if present
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

BACKUP_FILE="${1:-}"

if [ -z "$BACKUP_FILE" ]; then
    echo "Usage: $0 <backup_file>"
    echo ""
    echo "Available backups:"
    ls -lh "$PROJECT_ROOT/backups"/tipbot_*.sql.gz 2>/dev/null || echo "  No backups found"
    exit 1
fi

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: Backup file not found: $BACKUP_FILE"
    exit 1
fi

# Validate DATABASE_URL
if [ -z "${DATABASE_URL:-}" ]; then
    echo "ERROR: DATABASE_URL not set"
    exit 1
fi

# An archive is only worth restoring if it contains a dump. A `pg_dump` that died
# mid-pipe leaves a valid gzip of nothing, and piping that into psql answers
# "Restore completed successfully" having restored no rows. This is checked before
# anything is asked of the operator and before a client binary has to be found.
if ! pg_archive_is_real "$BACKUP_FILE"; then
    echo "ERROR: $BACKUP_FILE holds no PostgreSQL dump: nothing would be restored" >&2
    exit 1
fi

# psql is nowhere on PATH on a Windows host with a service install of PostgreSQL,
# which the backup script knew and this one did not: the recovery command died on
# "command not found" only when an operator actually needed it.
if ! pg_path_probe psql; then
    echo "ERROR: psql is not on PATH and no PostgreSQL install was found" >&2
    exit 1
fi

echo "WARNING: This will overwrite the current database!"
echo "Backup file: $BACKUP_FILE"
read -p "Continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted"
    exit 0
fi

echo "Restoring database from $BACKUP_FILE..."

# Decompress and restore
gunzip -c "$BACKUP_FILE" | psql "$DATABASE_URL" \
    --single-transaction \
    --variable=ON_ERROR_STOP=1

echo "Restore completed successfully"
echo "Run 'alembic upgrade head' if schema changed"
