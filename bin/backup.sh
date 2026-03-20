#!/bin/bash

set -euo pipefail

# Timestamp for logs and backup file naming
NOW=$(date +"%Y-%m-%d_%H-%M-%S")

# Load environment
if [ -f /home/boundys/data4/django/agile_predict/.env ]; then
    set -a
    . /home/boundys/data4/django/agile_predict/.env
    set +a
else
    echo "Missing .env file"
    exit 1
fi

# Ensure LOG_DIR, BACKUP_DIR, and BACKUPS_TO_KEEP are set
: "${LOG_DIR:?LOG_DIR not set in .env}"
: "${BACKUP_DIR:?BACKUP_DIR not set in .env}"
BACKUPS_TO_KEEP="${BACKUPS_TO_KEEP:-3}"

mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/backup_$NOW.log"

# Redirect all output to log file
exec > >(tee -a "$LOG_FILE") 2>&1

echo "================ $(date) ================"
echo "Starting backup script..."

# Ensure PATH includes pg_dump
export PATH="/usr/bin:/bin:$PATH"

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"

echo "DB_HOST: $DB_HOST"
echo "DB_PORT: $DB_PORT"

echo "Starting backup..."
export PGPASSWORD="$DB_PASSWORD"

mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_backup_$NOW.sql"
LATEST_BACKUP_FILE="$BACKUP_DIR/backup.sql"

/usr/bin/pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "Backup completed successfully: $BACKUP_FILE"
    cp "$BACKUP_FILE" "$LATEST_BACKUP_FILE"
    echo "Updated latest backup symlink: $LATEST_BACKUP_FILE"
else
    echo "Backup FAILED"
fi

# Cleanup old backups and their logs beyond $BACKUPS_TO_KEEP
BACKUP_PATTERN="$BACKUP_DIR/${DB_NAME}_backup_*.sql"
LOG_PATTERN="$LOG_DIR/backup_*.log"

BACKUP_LIST=$(ls -1t $BACKUP_PATTERN 2>/dev/null)
LOG_LIST=$(ls -1t $LOG_PATTERN 2>/dev/null)

DELETE_BACKUPS=$(echo "$BACKUP_LIST" | tail -n +$((BACKUPS_TO_KEEP + 1)))
DELETE_LOGS=$(echo "$LOG_LIST" | tail -n +$((BACKUPS_TO_KEEP + 1)))

echo "$DELETE_BACKUPS" | xargs -r rm --
echo "$DELETE_LOGS" | xargs -r rm --

echo "Old backups and logs cleaned. Keeping last $BACKUPS_TO_KEEP."