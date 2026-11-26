#!/bin/sh
# Forced SSH command for the user "demo" (see authorized_keys). demo01 can ONLY ask for the actions below;
# anything else is refused. The action itself runs as root through translog-demo-run (sudoers allows only that).
# shellcheck disable=SC2086
set -f                      # no wildcards from the command line
set -- $SSH_ORIGINAL_COMMAND
case "$1" in
    status|backup|attack|attack-encrypt|restore-list|locktest|s3-list|problems|rotate-keys)
        [ "$#" = 1 ] || { echo "refused"; exit 2; }
        exec sudo -n /usr/local/sbin/translog-demo-run "$1" ;;
    restore|rollback-preview|rollback)
        [ "$#" = 3 ] || { echo "refused"; exit 2; }
        exec sudo -n /usr/local/sbin/translog-demo-run "$1" "$2" "$3" ;;
    *)
        echo "refused: $1"; exit 2 ;;
esac
