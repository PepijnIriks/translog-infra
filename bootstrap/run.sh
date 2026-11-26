#!/usr/bin/env bash
# Applies this repository to THIS server: newest code from GitHub, then the server's own part of site.yml.
# Run by cloud-init at the first boot and every 30 minutes by translog-pull.timer (see role "common").
# By hand (as root):  bash /opt/translog-infra/bootstrap/run.sh
set -euo pipefail
REPO=/opt/translog-infra
cd "$REPO"
git fetch -q origin && git reset -q --hard origin/main
cd ansible
echo "== $(date '+%F %T') applying translog-infra $(git -C "$REPO" log --oneline -1) to $(hostname -s)"
ansible-playbook site.yml --limit "$(hostname -s)"
