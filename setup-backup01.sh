#!/usr/bin/env bash
# =============================================================================
#  setup-backup01.sh  -  TransLog ransomware-project, server backup01
#
#  Wat dit script doet:
#   1. Controleert of /srv/seaweedfs gekoppeld is (datavolume /dev/vdb)
#   2. Installeert SeaweedFS en maakt de systeemgebruiker 'seaweedfs'
#   3. Maakt S3-sleutels voor 'admin' en 'kopia' (kopia: alleen bucket translog-backups,
#      geen Admin-rechten)
#   4. Zet SeaweedFS op als systemd-service (S3 op poort 8333)
#   5. Maakt bucket 'translog-backups' met object lock (COMPLIANCE, 14 dagen)
#   6. Zelftest: back-up wissen moet MISLUKKEN (test T1), kopia-sleutel mag
#      object lock niet aanpassen (test T2)
#   7. Zet je SSH-sleutel neer (als /tmp/key.pub bestaat) en zet daarna
#      inloggen met wachtwoord uit
#   8. Zet de host-firewall (ufw) aan: S3 alleen vanaf app01, SSH alleen vanaf
#      beheer-tier en je OpenVPN-adres
#
#  Gebruik (op backup01):   sudo bash /tmp/setup-backup01.sh
#  Het script kan veilig opnieuw worden gedraaid; bestaande sleutels blijven staan.
# =============================================================================
set -euo pipefail

# ----------------------------- instellingen ----------------------------------
BACKUP_IP="10.20.3.198"          # backup01
APP_IP="10.20.1.254"             # app01  (enige die S3 mag gebruiken, eis E2)
MON_NET="10.20.2.0/24"           # beheer-tier (mon01)
ADMIN_SSH_SRC="172.16.16.0/20"   # OpenVPN-bereik EduCloud (adres wisselt per verbinding;
                                 # de ACL in EduCloud beperkt het tot jouw /32)
DATA_DIR="/srv/seaweedfs"
CONF_DIR="/etc/seaweedfs"
BUCKET="translog-backups"
LOCK_MODE="COMPLIANCE"           # COMPLIANCE = niemand kan wissen, ook admin niet (E1)
LOCK_DAYS=14
S3_PORT=8333
ENABLE_UFW=1                     # zet op 0 om de firewall-stap over te slaan
SSH_USER="ubuntu"
CRED_FILE="/root/seaweedfs-credentials.txt"
LOG="/var/log/setup-backup01.log"
# -----------------------------------------------------------------------------

exec > >(tee -a "$LOG") 2>&1
ok()   { echo -e "  \e[32m[OK]\e[0m   $*"; }
warn() { echo -e "  \e[33m[LET OP]\e[0m $*"; }
fail() { echo -e "  \e[31m[FOUT]\e[0m $*"; exit 1; }
step() { echo; echo -e "\e[1m== $* ==\e[0m"; }

[ "$(id -u)" -eq 0 ] || fail "Start dit script met sudo: sudo bash $0"
[ "$(hostname)" = "backup01" ] || warn "Hostname is '$(hostname)', niet backup01. Doorgaan over 5 sec (Ctrl+C om te stoppen)..."
[ "$(hostname)" = "backup01" ] || sleep 5

# --- 1. datavolume -----------------------------------------------------------
step "1/8 Datavolume controleren"
if mountpoint -q "$DATA_DIR"; then
  ok "$DATA_DIR is gekoppeld ($(df -h --output=size "$DATA_DIR" | tail -1 | tr -d ' '))"
else
  fail "$DATA_DIR is niet gekoppeld. Koppel eerst /dev/vdb (zie stap 1 in de chat)."
fi

# --- 2. pakketten en SeaweedFS -----------------------------------------------
step "2/8 Pakketten en SeaweedFS installeren"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates openssl python3 python3-boto3 ufw >/dev/null
ok "Pakketten geinstalleerd (curl, openssl, python3-boto3, ufw)"

if [ -x /usr/local/bin/weed ]; then
  ok "SeaweedFS staat er al: $(/usr/local/bin/weed version 2>/dev/null | head -1)"
else
  TMP=$(mktemp -d)
  curl -fsSL --retry 3 -o "$TMP/weed.tar.gz" \
    https://github.com/seaweedfs/seaweedfs/releases/latest/download/linux_amd64.tar.gz \
    || fail "Download mislukt. Mag backup01 naar internet (uitgaand TCP 443 in de ACL)?"
  tar -xzf "$TMP/weed.tar.gz" -C "$TMP"
  install -m 0755 "$TMP/weed" /usr/local/bin/weed
  rm -rf "$TMP"
  ok "SeaweedFS geinstalleerd: $(/usr/local/bin/weed version 2>/dev/null | head -1)"
fi

id seaweedfs >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin seaweedfs
chown -R seaweedfs:seaweedfs "$DATA_DIR"
mkdir -p "$CONF_DIR"
ok "Gebruiker 'seaweedfs' en rechten op $DATA_DIR in orde"

# --- 3. S3-sleutels ----------------------------------------------------------
step "3/8 S3-sleutels"
if [ -f "$CRED_FILE" ]; then
  # shellcheck disable=SC1090
  source "$CRED_FILE"
  ok "Bestaande sleutels gebruikt uit $CRED_FILE"
else
  ADMIN_KEY=$(openssl rand -hex 10);  ADMIN_SECRET=$(openssl rand -hex 20)
  KOPIA_KEY=$(openssl rand -hex 10);  KOPIA_SECRET=$(openssl rand -hex 20)
  umask 077
  cat > "$CRED_FILE" <<EOF
# SeaweedFS S3-sleutels backup01 - BEWAAR IN JE WACHTWOORDMANAGER
S3_ENDPOINT=http://${BACKUP_IP}:${S3_PORT}
ADMIN_KEY=${ADMIN_KEY}
ADMIN_SECRET=${ADMIN_SECRET}
KOPIA_KEY=${KOPIA_KEY}
KOPIA_SECRET=${KOPIA_SECRET}
EOF
  umask 022
  ok "Nieuwe sleutels gemaakt en opgeslagen in $CRED_FILE (alleen leesbaar voor root)"
fi

cat > "$CONF_DIR/s3.json" <<EOF
{
  "identities": [
    {
      "name": "admin",
      "credentials": [{ "accessKey": "${ADMIN_KEY}", "secretKey": "${ADMIN_SECRET}" }],
      "actions": ["Admin", "Read", "List", "Tagging", "Write"]
    },
    {
      "name": "kopia",
      "credentials": [{ "accessKey": "${KOPIA_KEY}", "secretKey": "${KOPIA_SECRET}" }],
      "actions": ["Read:${BUCKET}", "List:${BUCKET}", "Write:${BUCKET}"]
    }
  ]
}
EOF
chown seaweedfs:seaweedfs "$CONF_DIR/s3.json"
chmod 600 "$CONF_DIR/s3.json"
ok "$CONF_DIR/s3.json geschreven (kopia: alleen ${BUCKET}, geen Admin)"

# --- 4. systemd-service ------------------------------------------------------
step "4/8 SeaweedFS-service"
cat > /etc/systemd/system/seaweedfs.service <<EOF
[Unit]
Description=SeaweedFS (S3 met object lock) - TransLog backup01
After=network-online.target srv-seaweedfs.mount
Wants=network-online.target
RequiresMountsFor=${DATA_DIR}

[Service]
User=seaweedfs
Group=seaweedfs
ExecStart=/usr/local/bin/weed server -dir=${DATA_DIR} -ip=${BACKUP_IP} \\
  -master.volumeSizeLimitMB=1024 -volume.max=40 \\
  -s3 -s3.port=${S3_PORT} -s3.config=${CONF_DIR}/s3.json
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable seaweedfs >/dev/null 2>&1
systemctl restart seaweedfs

echo -n "  Wachten tot S3 antwoordt "
for i in $(seq 1 30); do
  if curl -s -o /dev/null "http://${BACKUP_IP}:${S3_PORT}/"; then echo; break; fi
  echo -n "."; sleep 2
  [ "$i" -eq 30 ] && { echo; journalctl -u seaweedfs -n 30 --no-pager; fail "S3 start niet (log hierboven)"; }
done
ok "SeaweedFS draait, S3 antwoordt op http://${BACKUP_IP}:${S3_PORT}"
sleep 5   # filer even laten opstarten

# --- 5 + 6. bucket met object lock en zelftest -------------------------------
step "5/8 Bucket '${BUCKET}' met object lock (${LOCK_MODE}, ${LOCK_DAYS} dagen)"
export EP="http://${BACKUP_IP}:${S3_PORT}" BUCKET LOCK_MODE LOCK_DAYS \
       ADMIN_KEY ADMIN_SECRET KOPIA_KEY KOPIA_SECRET
set +e
python3 - <<'PY'
import os, sys, time, datetime
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ep, b = os.environ["EP"], os.environ["BUCKET"]
mode, days = os.environ["LOCK_MODE"], int(os.environ["LOCK_DAYS"])
cfg = Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5})
def client(k, s):
    return boto3.client("s3", endpoint_url=ep, aws_access_key_id=k,
                        aws_secret_access_key=s, region_name="us-east-1", config=cfg)
admin = client(os.environ["ADMIN_KEY"], os.environ["ADMIN_SECRET"])
kopia = client(os.environ["KOPIA_KEY"], os.environ["KOPIA_SECRET"])
G, R, Y, N = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
def ok(m):   print(f"  {G}[OK]{N}   {m}")
def bad(m):  print(f"  {R}[FOUT]{N} {m}")
def warn(m): print(f"  {Y}[LET OP]{N} {m}")
errors = 0

# bucket aanmaken
try:
    admin.create_bucket(Bucket=b, ObjectLockEnabledForBucket=True)
    ok(f"Bucket '{b}' aangemaakt met object lock")
except ClientError as e:
    code = e.response["Error"]["Code"]
    if code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
        ok(f"Bucket '{b}' bestond al")
    else:
        bad(f"Bucket aanmaken mislukt: {code} {e}"); sys.exit(1)

try:
    admin.put_bucket_versioning(Bucket=b, VersioningConfiguration={"Status": "Enabled"})
except ClientError as e:
    warn(f"Versioning zetten gaf: {e.response['Error']['Code']} (bij object lock staat het vaak al aan)")
v = admin.get_bucket_versioning(Bucket=b).get("Status")
(ok if v == "Enabled" else bad)(f"Versioning: {v}")
if v != "Enabled": errors += 1

try:
    admin.put_object_lock_configuration(Bucket=b, ObjectLockConfiguration={
        "ObjectLockEnabled": "Enabled",
        "Rule": {"DefaultRetention": {"Mode": mode, "Days": days}}})
    c = admin.get_object_lock_configuration(Bucket=b)["ObjectLockConfiguration"]
    r = c.get("Rule", {}).get("DefaultRetention", {})
    ok(f"Standaard bewaartijd: {r.get('Mode')} {r.get('Days')} dagen")
except ClientError as e:
    bad(f"Object lock instellen mislukt: {e.response['Error']['Code']} {e}"); errors += 1

print("\n\033[1m== 6/8 Zelftest (T1 en T2) ==\033[0m")
key = f"selftest/T1-{datetime.datetime.now():%Y%m%d-%H%M%S}.txt"
put = kopia.put_object(Bucket=b, Key=key, Body=b"TransLog zelftest object lock\n")
vid = put.get("VersionId")
ok(f"Kopia-sleutel kan schrijven: {key} (versie {vid})")
h = admin.head_object(Bucket=b, Key=key, VersionId=vid) if vid else admin.head_object(Bucket=b, Key=key)
ok(f"Object vergrendeld: {h.get('ObjectLockMode')} tot {h.get('ObjectLockRetainUntilDate')}")
if not h.get("ObjectLockMode"):
    bad("Object heeft GEEN lock gekregen"); errors += 1

# T1a: kopia probeert versie te wissen
for who, cl in (("kopia", kopia), ("admin", admin)):
    try:
        cl.delete_object(Bucket=b, Key=key, VersionId=vid)
        bad(f"T1: {who} kon de vergrendelde versie WISSEN - object lock werkt niet!"); errors += 1
    except ClientError as e:
        ok(f"T1: {who} kan vergrendelde versie niet wissen ({e.response['Error']['Code']})")

# T1b: overschrijven = nieuwe versie, oude blijft
kopia.put_object(Bucket=b, Key=key, Body=b"VERSLEUTELD door aanvaller\n")
vs = admin.list_object_versions(Bucket=b, Prefix=key).get("Versions", [])
(ok if len(vs) >= 2 else bad)(f"T1: overschrijven maakt nieuwe versie, oude blijft bestaan ({len(vs)} versies)")
if len(vs) < 2: errors += 1

# T2: kopia mag lock-instellingen niet aanpassen of bucket wissen
try:
    kopia.put_object_lock_configuration(Bucket=b, ObjectLockConfiguration={
        "ObjectLockEnabled": "Enabled",
        "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}})
    bad("T2: kopia-sleutel kon object lock AANPASSEN"); errors += 1
except ClientError as e:
    ok(f"T2: kopia kan object lock niet aanpassen ({e.response['Error']['Code']})")
try:
    kopia.delete_bucket(Bucket=b)
    bad("T2: kopia-sleutel kon de bucket WISSEN"); errors += 1
except ClientError as e:
    ok(f"T2: kopia kan de bucket niet wissen ({e.response['Error']['Code']})")

sys.exit(1 if errors else 0)
PY
SELFTEST=$?
set -e

# --- 7. SSH-sleutel en wachtwoord-login ---------------------------------------
step "7/8 SSH"
HOME_DIR=$(getent passwd "$SSH_USER" | cut -d: -f6)
if [ -f /tmp/key.pub ]; then
  install -d -m 700 -o "$SSH_USER" -g "$SSH_USER" "$HOME_DIR/.ssh"
  touch "$HOME_DIR/.ssh/authorized_keys"
  grep -qxF "$(cat /tmp/key.pub)" "$HOME_DIR/.ssh/authorized_keys" || cat /tmp/key.pub >> "$HOME_DIR/.ssh/authorized_keys"
  chown "$SSH_USER:$SSH_USER" "$HOME_DIR/.ssh/authorized_keys"; chmod 600 "$HOME_DIR/.ssh/authorized_keys"
  rm -f /tmp/key.pub
  ok "SSH-sleutel toegevoegd voor $SSH_USER"
fi
if [ -s "$HOME_DIR/.ssh/authorized_keys" ]; then
  echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/99-translog.conf
  for f in /etc/ssh/sshd_config.d/*.conf; do
    [ "$f" = /etc/ssh/sshd_config.d/99-translog.conf ] && continue
    sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' "$f"
  done
  sshd -t && systemctl restart ssh
  ok "Inloggen met wachtwoord uitgezet (alleen nog met sleutel; console blijft werken)"
else
  warn "Geen SSH-sleutel gevonden - wachtwoord-login NIET uitgezet"
fi

# --- 8. host-firewall --------------------------------------------------------
step "8/8 Host-firewall (ufw)"
if [ "$ENABLE_UFW" = "1" ]; then
  ufw --force reset >/dev/null
  ufw default deny incoming  >/dev/null
  ufw default allow outgoing >/dev/null
  ufw allow from "$MON_NET"       to any port 22 proto tcp comment 'SSH vanaf beheer-tier' >/dev/null
  ufw allow from "$ADMIN_SSH_SRC" to any port 22 proto tcp comment 'SSH vanaf OpenVPN laptop' >/dev/null
  ufw allow from "$APP_IP"        to any port "$S3_PORT" proto tcp comment 'S3 alleen vanaf app01' >/dev/null
  ufw allow from "$MON_NET"       to any port 10050 proto tcp comment 'Zabbix-agent vanaf beheer' >/dev/null
  ufw --force enable >/dev/null
  ok "ufw actief: SSH vanaf $MON_NET en $ADMIN_SSH_SRC, S3 ($S3_PORT) alleen vanaf $APP_IP, Zabbix-agent (10050) vanaf $MON_NET"
else
  warn "ufw overgeslagen (ENABLE_UFW=0)"
fi

# --- samenvatting ------------------------------------------------------------
echo
echo "============================================================"
if [ "$SELFTEST" -eq 0 ]; then
  echo -e "  \e[32mKLAAR - backup01 is ingericht en alle zelftests zijn geslaagd\e[0m"
else
  echo -e "  \e[31mKLAAR MET FOUTEN - zie [FOUT]-regels hierboven\e[0m"
fi
echo "  S3-endpoint : http://${BACKUP_IP}:${S3_PORT}"
echo "  Bucket      : ${BUCKET} (${LOCK_MODE}, ${LOCK_DAYS} dagen)"
echo "  Sleutels    : sudo cat ${CRED_FILE}   (niet in de chat plakken!)"
echo "  Log         : ${LOG}"
echo "============================================================"
