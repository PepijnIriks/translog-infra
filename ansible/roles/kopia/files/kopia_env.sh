#!/usr/bin/env bash
# Sourced by the TransLog back-up scripts: puts the back-up login (from /etc/translog/backup.json, picked up
# once from backup01) in environment variables, so it never shows up in a command line or a log.
set -a
BUCKET=$(jq -r .bucket /etc/translog/backup.json)
PREFIX=$(cat /etc/translog/kopia-prefix 2>/dev/null || jq -r '.prefix // "translog/"' /etc/translog/backup.json)
ENDPOINT=$(jq -r .endpoint /etc/translog/backup.json)
RETENTION_DAYS=$(jq -r .retention_days /etc/translog/backup.json)
AWS_ACCESS_KEY_ID=$(jq -r .access_key /etc/translog/backup.json)
AWS_SECRET_ACCESS_KEY=$(jq -r .secret_key /etc/translog/backup.json)
KOPIA_PASSWORD=$(jq -r .repo_password /etc/translog/backup.json)
KOPIA_CHECK_FOR_UPDATES=false
# systemd starts the nightly back-up without HOME: point Kopia to root's config explicitly,
# otherwise it says "repository is not connected"
HOME=${HOME:-/root}
KOPIA_CONFIG_PATH=/root/.config/kopia/repository.config
set +a
