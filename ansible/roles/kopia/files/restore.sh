#!/usr/bin/env bash
# Restores the fileshare and the database of app01 from backup01, from the newest snapshot made BEFORE a given
# time (the time of the attack), so no encrypted files come back (E6).
#
#   translog-restore --list                         show the snapshots
#   translog-restore --before "2026-12-10 14:00"    restore from the newest snapshot before that time
#
# Afterwards it checks that no *.locked file is left and that the portal answers.
set -euo pipefail
. /usr/local/sbin/translog-kopia-env

if [ "${1:-}" = "--list" ]; then
    kopia snapshot list --all
    exit 0
fi
[ "${1:-}" = "--before" ] && [ -n "${2:-}" ] || { echo "usage: translog-restore --list | --before \"YYYY-MM-DD HH:MM\""; exit 2; }
BEFORE=$(date -d "$2" +%s)
START=$(date +%s)

pick() {   # newest snapshot of a path made before $BEFORE
    kopia snapshot list "$1" --json \
      | jq -r --argjson b "$BEFORE" '[.[] | select((.startTime | sub("\\.[0-9]+";"") | fromdateiso8601) < $b)] | sort_by(.startTime) | last | .id // empty'
}
FS=$(pick /srv/fileshare)
DB=$(pick /var/backups/translog)
[ -n "$FS" ] && [ -n "$DB" ] || { echo "No snapshot before $2"; exit 1; }
echo "Restoring fileshare from snapshot $FS and database from snapshot $DB"

systemctl stop translog-portal
# Fileshare: replace everything with the snapshot
find /srv/fileshare -mindepth 1 -delete
kopia snapshot restore "$FS" /srv/fileshare
chown -R translog:translog /srv/fileshare
setfacl -R -m u:zabbix:rX -m d:u:zabbix:rX /srv/fileshare

# Database: restore the dump
kopia snapshot restore "$DB" /var/backups/translog/restore
sudo -u postgres pg_restore --clean --if-exists -d translog /var/backups/translog/restore/translog.dump
rm -rf /var/backups/translog/restore
systemctl start translog-portal
sleep 3

LOCKED=$(find /srv/fileshare -name '*.locked' | wc -l)
HEALTH=$(curl -s http://127.0.0.1/health || true)
echo "Restore took $(( $(date +%s) - START )) s. Encrypted files left: $LOCKED. Portal: $HEALTH"
[ "$LOCKED" = 0 ]
