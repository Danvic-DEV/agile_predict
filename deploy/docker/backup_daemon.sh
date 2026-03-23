#!/usr/bin/env sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-/config/backups}"
LOG_DIR="${LOG_DIR:-/config/logs}"
BACKUP_HOUR="${BACKUP_HOUR:-3}"
BACKUPS_TO_KEEP="${BACKUPS_TO_KEEP:-7}"

mkdir -p "${BACKUP_DIR}" "${LOG_DIR}"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_DIR}/backup_daemon.log"
}

run_backup() {
    NOW=$(date +"%Y-%m-%d_%H-%M-%S")
    BACKUP_FILE="${BACKUP_DIR}/agile_predict_backup_${NOW}.sql"
    LATEST_LINK="${BACKUP_DIR}/latest.sql"
    LOG_FILE="${LOG_DIR}/backup_${NOW}.log"
    
    log "Starting backup to ${BACKUP_FILE}"
    
    if pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" > "${BACKUP_FILE}" 2>"${LOG_FILE}"; then
        log "Backup completed successfully: ${BACKUP_FILE}"
        ln -sf "$(basename "${BACKUP_FILE}")" "${LATEST_LINK}"
        
        # Cleanup old backups
        BACKUP_COUNT=$(ls -1 "${BACKUP_DIR}"/agile_predict_backup_*.sql 2>/dev/null | wc -l)
        if [ "${BACKUP_COUNT}" -gt "${BACKUPS_TO_KEEP}" ]; then
            ls -1t "${BACKUP_DIR}"/agile_predict_backup_*.sql | tail -n +$((BACKUPS_TO_KEEP + 1)) | xargs rm -f
            log "Cleaned up old backups, keeping last ${BACKUPS_TO_KEEP}"
        fi
        
        # Cleanup old logs
        LOG_COUNT=$(ls -1 "${LOG_DIR}"/backup_*.log 2>/dev/null | wc -l)
        if [ "${LOG_COUNT}" -gt "${BACKUPS_TO_KEEP}" ]; then
            ls -1t "${LOG_DIR}"/backup_*.log | tail -n +$((BACKUPS_TO_KEEP + 1)) | xargs rm -f
        fi
    else
        log "ERROR: Backup failed, check ${LOG_FILE}"
    fi
}

seconds_until_target_hour() {
    current_hour=$(date +%H | sed 's/^0//')
    current_minute=$(date +%M | sed 's/^0//')
    current_second=$(date +%S | sed 's/^0//')
    
    target_hour="${BACKUP_HOUR}"
    
    current_seconds=$((current_hour * 3600 + current_minute * 60 + current_second))
    target_seconds=$((target_hour * 3600))
    
    if [ "${current_seconds}" -lt "${target_seconds}" ]; then
        echo $((target_seconds - current_seconds))
    else
        echo $((86400 - current_seconds + target_seconds))
    fi
}

log "Backup daemon started (backups scheduled for ${BACKUP_HOUR}:00 daily)"
log "Backup directory: ${BACKUP_DIR}"
log "Retention: ${BACKUPS_TO_KEEP} backups"

while true; do
    sleep_seconds=$(seconds_until_target_hour)
    log "Next backup in ${sleep_seconds} seconds ($(date -d "@$(($(date +%s) + sleep_seconds))" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || date -r $(($(date +%s) + sleep_seconds)) '+%Y-%m-%d %H:%M:%S'))"
    sleep "${sleep_seconds}"
    run_backup
    sleep 60  # Prevent multiple runs if script executes near target time
done
