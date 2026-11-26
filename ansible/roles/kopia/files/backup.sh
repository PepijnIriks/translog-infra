#!/usr/bin/env bash
# TransLog nightly back-up: database dump + fileshare -> Kopia repository on backup01 (object lock).
# Writes its result for Zabbix: /var/lib/translog/backup-status (0 = OK) and backup-last-ok (epoch).
set -uo pipefail
LOG=/var/log/translog-backup.log
DUMP=/var/backups/translog
START=$(date +%s)
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

finish() {
    echo "$1" > /var/lib/translog/backup-status
    [ "$1" = 0 ] && date +%s > /var/lib/translog/backup-last-ok
    log "back-up finished with code $1 after $(( $(date +%s) - START )) s"
    exit "$1"
}

log "back-up started"
. /usr/local/sbin/translog-kopia-env || finish 10

# 1. Database dump (custom format, consistent while the portal keeps running)
sudo -u postgres pg_dump -Fc translog > "$DUMP/translog.dump.tmp" 2>> "$LOG" || finish 11
mv "$DUMP/translog.dump.tmp" "$DUMP/translog.dump"

# 2. Snapshot of the fileshare and the dump (encrypted by Kopia before it leaves this server)
kopia snapshot create /srv/fileshare "$DUMP" --description "nightly $(date +%F)" >> "$LOG" 2>&1 || finish 12

# 3. Short check that the newest snapshot can be read back
kopia snapshot verify --verify-files-percent=1 >> "$LOG" 2>&1 || finish 13

finish 0
