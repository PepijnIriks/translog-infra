#!/usr/bin/env bash
# Runs one demo action on THIS server for the demo website (demo01). Started only through translog-demo-cmd.
# Every action is one of the existing TransLog tools; this script only checks the input and starts it.
set -uo pipefail
export PYTHONUNBUFFERED=1
HOST=$(hostname -s)
VERB=${1:-}
WHEN="${2:-} ${3:-}"
time_ok() { [[ "$WHEN" =~ ^20[0-9]{2}-[0-1][0-9]-[0-3][0-9]\ [0-2][0-9]:[0-5][0-9]$ ]] || { echo "invalid time"; exit 2; }; }
only() { [ "$HOST" = "$1" ] || { echo "not on $HOST"; exit 2; }; }
[ "$VERB" = status ] || [ "$VERB" = problems ] || echo "[$HOST $(date '+%H:%M:%S')] $VERB ${2:-} ${3:-}"

case "$VERB" in
status)
    case "$HOST" in
    app01)
        HEALTH=$(curl -s -m 5 http://127.0.0.1/health)
        jq -e . >/dev/null 2>&1 <<<"$HEALTH" || HEALTH='{}'
        LOCKED=$(find /srv/fileshare -name '*.locked' 2>/dev/null | wc -l)
        FILES=$(find /srv/fileshare -type f 2>/dev/null | wc -l)
        jq -n --argjson h "$HEALTH" --arg st "$(cat /var/lib/translog/backup-status 2>/dev/null)" \
              --arg ok "$(cat /var/lib/translog/backup-last-ok 2>/dev/null)" --arg locked "$LOCKED" --arg files "$FILES" \
              --arg att "$(tail -n 1 /var/log/translog-attack.log 2>/dev/null)" \
              --arg run "$(systemctl is-active translog-backup 2>/dev/null)" \
              '{health:$h, backup_status:$st, backup_last_ok:$ok, locked:($locked|tonumber), files:($files|tonumber),
                last_attack_line:$att, backup_running:($run=="activating")}' ;;
    backup01) /usr/local/sbin/translog-s3-list --summary-json ;;
    mon01)    /usr/local/sbin/translog-zabbix-problems ;;
    *)        echo '{}' ;;
    esac ;;
backup)          only app01; /usr/local/sbin/translog-backup; echo "exit code $?"; tail -n 12 /var/log/translog-backup.log ;;
attack)          only app01; /usr/local/sbin/translog-attack-sim --ik-weet-het-zeker ;;
attack-encrypt)  only app01; /usr/local/sbin/translog-attack-sim --ik-weet-het-zeker --only encrypt ;;
restore-list)    only app01; /usr/local/sbin/translog-restore --list ;;
restore)         only app01; time_ok; /usr/local/sbin/translog-restore --before "$WHEN" ;;
locktest)        only backup01; /usr/local/sbin/translog-locktest ;;
s3-list)         only backup01; /usr/local/sbin/translog-s3-list --prefix app01/ --max 25 ;;
rollback-preview) only backup01; time_ok; /usr/local/sbin/translog-s3-rollback --before "$WHEN" ;;
rollback)        only backup01; time_ok; /usr/local/sbin/translog-s3-rollback --before "$WHEN" --doe-het ;;
rotate-keys)     only backup01; /usr/local/sbin/translog-rotate-keys ;;
problems)        only mon01; /usr/local/sbin/translog-zabbix-problems ;;
*)               echo "unknown action"; exit 2 ;;
esac
