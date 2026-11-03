#!/usr/bin/env bash
# =============================================================================
#  setup-app01.sh  -  TransLog ransomware-project, server app01 (productie)
#
#  Wat dit script doet:
#   1. Vraagt de Kopia-sleutel van backup01 en test de verbinding met S3
#   2. Installeert PostgreSQL, nginx, het portaal (Flask), Samba en Kopia
#   3. Database 'translog' met klanten en zendingen (verzonnen testdata)
#   4. Portaal "zendingen volgen" op poort 80 (+ /health voor monitoring)
#   5. Fileshare \\app01\translog met planningen en contracten
#   6. Kopia-repository op backup01 met object lock (COMPLIANCE, 14 dagen)
#   7. Back-upjob elke nacht om 01:00 (database-dump + bestanden + config)
#      en een eerste back-up direct
#   8. Hersteltest-script (translog-restore-test)
#   9. SSH-sleutel, wachtwoord-login uit, host-firewall (ufw)
#
#  Gebruik (op app01):   sudo bash /tmp/setup-app01.sh
#  Opnieuw draaien kan; bestaande wachtwoorden en de repository blijven staan.
# =============================================================================
set -euo pipefail

# ----------------------------- instellingen ----------------------------------
APP_IP="10.20.1.254"
BACKUP_IP="10.20.3.198"
S3_PORT=8333
BUCKET="translog-backups"
KOPIA_PREFIX="app01/"
LOCK_MODE="COMPLIANCE"
LOCK_PERIOD="14d"
MON_NET="10.20.2.0/24"           # beheer-tier (mon01)
VPC_NET="10.20.0.0/16"           # hele VPC
VPN_NET="172.16.16.0/20"         # OpenVPN EduCloud (laptop)
BACKUP_TIME="01:00"              # instance-back-up EduCloud draait om 02:00
SSH_USER="ubuntu"
CRED_FILE="/root/app01-credentials.txt"
PORTAL_DIR="/opt/translog-portal"
SHARE_DIR="/srv/share"
DUMP_DIR="/var/backups/translog"
STATUS_DIR="/var/lib/translog-backup"
LOG="/var/log/setup-app01.log"
# -----------------------------------------------------------------------------

exec > >(tee -a "$LOG") 2>&1
ok()   { echo -e "  \e[32m[OK]\e[0m   $*"; }
warn() { echo -e "  \e[33m[LET OP]\e[0m $*"; }
fail() { echo -e "  \e[31m[FOUT]\e[0m $*"; exit 1; }
step() { echo; echo -e "\e[1m== $* ==\e[0m"; }
rand() { openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c "$1"; }

[ "$(id -u)" -eq 0 ] || fail "Start dit script met sudo: sudo bash $0"
[ "$(hostname)" = "app01" ] || { warn "Hostname is '$(hostname)', niet app01. Doorgaan over 5 sec (Ctrl+C = stoppen)"; sleep 5; }

# --- 1. gegevens en verbinding -----------------------------------------------
step "1/9 Kopia-sleutel en verbinding met backup01"
if [ -f "$CRED_FILE" ]; then
  # shellcheck disable=SC1090
  source "$CRED_FILE"
  ok "Bestaande gegevens gebruikt uit $CRED_FILE"
else
  echo "  Typ de Kopia-sleutel uit Bitwarden (TransLog - S3 kopia)."
  read -rp  "  KOPIA_KEY    : " KOPIA_KEY
  read -rsp "  KOPIA_SECRET : " KOPIA_SECRET; echo
  [ -n "$KOPIA_KEY" ] && [ -n "$KOPIA_SECRET" ] || fail "Sleutel of secret is leeg"
  KOPIA_REPO_PASSWORD=$(rand 32)
  PG_PORTAL_PASSWORD=$(rand 24)
  SMB_PASSWORD=$(rand 20)
  umask 077
  cat > "$CRED_FILE" <<EOF
# app01 - BEWAAR IN BITWARDEN (map TransLog)
# KOPIA_REPO_PASSWORD versleutelt ALLE back-ups: kwijt = geen herstel!
KOPIA_KEY=${KOPIA_KEY}
KOPIA_SECRET=${KOPIA_SECRET}
KOPIA_REPO_PASSWORD=${KOPIA_REPO_PASSWORD}
PG_PORTAL_PASSWORD=${PG_PORTAL_PASSWORD}
SMB_USER=translog
SMB_PASSWORD=${SMB_PASSWORD}
EOF
  umask 022
  ok "Gegevens opgeslagen in $CRED_FILE (alleen leesbaar voor root)"
fi

curl -s -m 5 -o /dev/null "http://${BACKUP_IP}:${S3_PORT}/" \
  || fail "backup01 (S3 ${BACKUP_IP}:${S3_PORT}) niet bereikbaar. Check acl-productie (egress) en acl-backup regel 1."
ok "backup01 S3 bereikbaar op ${BACKUP_IP}:${S3_PORT}"

# --- 2. pakketten ------------------------------------------------------------
step "2/9 Pakketten installeren"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || fail "apt update mislukt. Heeft app01 internet (acl-productie egress)?"
apt-get install -y -qq curl ca-certificates gnupg openssl postgresql nginx \
  python3-flask python3-psycopg2 gunicorn samba ufw >/dev/null
ok "PostgreSQL, nginx, Flask, gunicorn, Samba en ufw geinstalleerd"

if ! command -v kopia >/dev/null; then
  install -d -m 0755 /etc/apt/keyrings
  if curl -fsSL https://kopia.io/signing-key -o /tmp/kopia.key; then
    gpg --dearmor --yes -o /etc/apt/keyrings/kopia-keyring.gpg /tmp/kopia.key
    echo "deb [signed-by=/etc/apt/keyrings/kopia-keyring.gpg] http://packages.kopia.io/apt/ stable main" \
      > /etc/apt/sources.list.d/kopia.list
    apt-get update -qq && apt-get install -y -qq kopia >/dev/null || true
  fi
  if ! command -v kopia >/dev/null; then
    warn "apt-repository van Kopia niet gelukt, download van GitHub"
    V=$(curl -fsSI https://github.com/kopia/kopia/releases/latest | grep -i '^location:' | sed 's#.*/v##; s#\r##')
    curl -fsSL -o /tmp/kopia.tgz "https://github.com/kopia/kopia/releases/download/v${V}/kopia-${V}-linux-x64.tar.gz" \
      || fail "Kopia downloaden mislukt"
    tar -xzf /tmp/kopia.tgz -C /tmp
    install -m 0755 "/tmp/kopia-${V}-linux-x64/kopia" /usr/local/bin/kopia
  fi
fi
ok "Kopia: $(kopia --version 2>/dev/null | head -1)"

# --- 3. database -------------------------------------------------------------
step "3/9 Database 'translog'"
systemctl enable --now postgresql >/dev/null 2>&1
sudo -u postgres psql -qtAc "SELECT 1 FROM pg_roles WHERE rolname='portal'" | grep -q 1 \
  || sudo -u postgres psql -qc "CREATE ROLE portal LOGIN PASSWORD '${PG_PORTAL_PASSWORD}'"
sudo -u postgres psql -qtAc "SELECT 1 FROM pg_database WHERE datname='translog'" | grep -q 1 \
  || sudo -u postgres createdb -O portal translog
sudo -u postgres psql -q -d translog <<'SQL'
CREATE TABLE IF NOT EXISTS klanten (
  id SERIAL PRIMARY KEY, naam TEXT NOT NULL, plaats TEXT NOT NULL, email TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zendingen (
  id SERIAL PRIMARY KEY, trackingcode TEXT UNIQUE NOT NULL,
  klant_id INT REFERENCES klanten(id), status TEXT NOT NULL,
  van TEXT NOT NULL, naar TEXT NOT NULL, gewijzigd TIMESTAMPTZ DEFAULT now());
INSERT INTO klanten (naam, plaats, email)
SELECT 'Testklant ' || g, (ARRAY['Eindhoven','Tilburg','Venlo','Breda','Utrecht'])[1 + g % 5],
       'klant' || g || '@voorbeeld.test'
FROM generate_series(1, 25) g
WHERE NOT EXISTS (SELECT 1 FROM klanten);
INSERT INTO zendingen (trackingcode, klant_id, status, van, naar)
SELECT 'TL' || lpad(g::text, 6, '0'), 1 + g % 25,
       (ARRAY['Aangemeld','Opgehaald','Onderweg','In depot','Bezorgd'])[1 + g % 5],
       (ARRAY['Eindhoven','Venlo','Rotterdam'])[1 + g % 3],
       (ARRAY['Tilburg','Breda','Utrecht','Antwerpen','Duisburg'])[1 + g % 5]
FROM generate_series(1, 500) g
WHERE NOT EXISTS (SELECT 1 FROM zendingen);
GRANT SELECT, INSERT, UPDATE ON klanten, zendingen TO portal;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO portal;
SQL
ok "Database translog: $(sudo -u postgres psql -qtAd translog -c 'SELECT count(*) FROM zendingen') zendingen, $(sudo -u postgres psql -qtAd translog -c 'SELECT count(*) FROM klanten') klanten (testdata)"

# --- 4. portaal --------------------------------------------------------------
step "4/9 Portaal 'zendingen volgen'"
id portal >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin portal
install -d -m 0755 "$PORTAL_DIR"
cat > "$PORTAL_DIR/app.py" <<'PYAPP'
import os, time
from contextlib import closing
import psycopg2
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)
DSN = os.environ["PORTAL_DSN"]

PAGE = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<title>TransLog - zending volgen</title>
<style>body{font-family:sans-serif;max-width:760px;margin:40px auto;color:#222}
h1{color:#1f3864}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:6px}
th{background:#1f3864;color:#fff}input{padding:6px;width:220px}.ok{color:#2e7d32}</style></head>
<body><h1>TransLog B.V. - zending volgen</h1>
<form><input name="code" placeholder="Trackingcode, bijv. TL000042" value="{{code}}">
<button>Zoeken</button></form>
{% if code %}{% if z %}
<table><tr><th>Trackingcode</th><th>Status</th><th>Van</th><th>Naar</th><th>Laatst gewijzigd</th></tr>
<tr><td>{{z[0]}}</td><td>{{z[1]}}</td><td>{{z[2]}}</td><td>{{z[3]}}</td><td>{{z[4].strftime('%d-%m-%Y %H:%M')}}</td></tr></table>
{% else %}<p>Geen zending gevonden voor {{code}}.</p>{% endif %}{% endif %}
<p class="ok">Systeemstatus: {{n}} zendingen in het systeem.</p></body></html>"""

def db():
    return psycopg2.connect(DSN, connect_timeout=3)

@app.route("/")
def index():
    code = request.args.get("code", "").strip().upper()[:12]
    with closing(db()) as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM zendingen"); n = cur.fetchone()[0]
        z = None
        if code:
            cur.execute("SELECT trackingcode,status,van,naar,gewijzigd FROM zendingen WHERE trackingcode=%s", (code,))
            z = cur.fetchone()
    return render_template_string(PAGE, code=code, z=z, n=n)

@app.route("/health")
def health():
    t = time.time()
    try:
        with closing(db()) as c, c.cursor() as cur:
            cur.execute("SELECT count(*) FROM zendingen"); n = cur.fetchone()[0]
        return jsonify(status="ok", zendingen=n, db_ms=round((time.time() - t) * 1000, 1))
    except Exception as e:
        return jsonify(status="error", error=str(e)[:200]), 503
PYAPP
cat > /etc/translog-portal.env <<EOF
PORTAL_DSN=host=127.0.0.1 dbname=translog user=portal password=${PG_PORTAL_PASSWORD}
EOF
chown root:portal /etc/translog-portal.env; chmod 640 /etc/translog-portal.env
cat > /etc/systemd/system/translog-portal.service <<EOF
[Unit]
Description=TransLog portaal (gunicorn)
After=network-online.target postgresql.service
Wants=postgresql.service

[Service]
User=portal
Group=portal
EnvironmentFile=/etc/translog-portal.env
WorkingDirectory=${PORTAL_DIR}
ExecStart=/usr/bin/gunicorn --workers 3 --bind 127.0.0.1:8000 app:app
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
cat > /etc/nginx/sites-available/translog <<'EOF'
server {
    listen 80 default_server;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
EOF
ln -sf /etc/nginx/sites-available/translog /etc/nginx/sites-enabled/translog
rm -f /etc/nginx/sites-enabled/default
systemctl daemon-reload
systemctl enable --now translog-portal >/dev/null 2>&1; systemctl restart translog-portal
nginx -t >/dev/null 2>&1 || fail "nginx-configuratie klopt niet"
systemctl enable nginx >/dev/null 2>&1; systemctl restart nginx
sleep 2
curl -fs http://127.0.0.1/health >/dev/null && ok "Portaal draait: http://${APP_IP}/  (health: $(curl -s http://127.0.0.1/health))" \
  || fail "Portaal antwoordt niet. Zie: journalctl -u translog-portal -n 30"

# --- 5. fileshare ------------------------------------------------------------
step "5/9 Fileshare (Samba)"
id translog >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin translog
install -d -m 2770 -o translog -g translog "$SHARE_DIR" "$SHARE_DIR/planning" "$SHARE_DIR/contracten"
if [ -z "$(ls -A "$SHARE_DIR/planning")" ]; then
  for d in maandag dinsdag woensdag donderdag vrijdag; do
    printf 'Planning %s\nRit;Chauffeur;Van;Naar\n1;Chauffeur A;Eindhoven;Tilburg\n2;Chauffeur B;Venlo;Duisburg\n' "$d" \
      > "$SHARE_DIR/planning/planning-$d.csv"
  done
  for i in $(seq 1 10); do
    printf 'Contract %03d\nTestklant %d - raamovereenkomst transport (testdocument)\n' "$i" "$i" \
      > "$SHARE_DIR/contracten/contract-$(printf %03d "$i").txt"
  done
  chown -R translog:translog "$SHARE_DIR"
fi
if ! grep -q '^\[translog\]' /etc/samba/smb.conf; then
  cat >> /etc/samba/smb.conf <<EOF

[translog]
   path = ${SHARE_DIR}
   valid users = translog
   read only = no
   force group = translog
   create mask = 0660
   directory mask = 2770
EOF
fi
printf '%s\n%s\n' "$SMB_PASSWORD" "$SMB_PASSWORD" | smbpasswd -s -a translog >/dev/null
systemctl enable smbd >/dev/null 2>&1; systemctl restart smbd
ok "Share \\\\${APP_IP}\\translog met $(find "$SHARE_DIR" -type f | wc -l) bestanden (gebruiker: translog)"

# --- 6. kopia-repository -----------------------------------------------------
step "6/9 Kopia-repository op backup01 (${LOCK_MODE}, ${LOCK_PERIOD})"
export KOPIA_PASSWORD="$KOPIA_REPO_PASSWORD"
KOPIA_S3=(s3 --bucket="$BUCKET" --prefix="$KOPIA_PREFIX" --endpoint="${BACKUP_IP}:${S3_PORT}"
          --disable-tls --access-key="$KOPIA_KEY" --secret-access-key="$KOPIA_SECRET")
if kopia repository status >/dev/null 2>&1; then
  ok "Kopia is al verbonden met de repository"
elif kopia repository connect "${KOPIA_S3[@]}" >/dev/null 2>&1; then
  ok "Verbonden met bestaande repository"
else
  kopia repository create "${KOPIA_S3[@]}" \
    --retention-mode="$LOCK_MODE" --retention-period="$LOCK_PERIOD" >/dev/null \
    || fail "Repository aanmaken mislukt. Heeft de kopia-sleutel op backup01 de rechten Get/PutObjectRetention? (draai setup-backup01.sh opnieuw)"
  ok "Nieuwe repository gemaakt (versleuteld, object lock ${LOCK_MODE} ${LOCK_PERIOD})"
fi
kopia policy set --global --keep-latest 10 --keep-hourly 0 --keep-daily 14 \
  --keep-weekly 8 --keep-monthly 6 --keep-annual 0 --compression=zstd >/dev/null
kopia maintenance set --owner=me --extend-object-locks=true >/dev/null
ok "Bewaarbeleid: 14 dagelijkse, 8 wekelijkse, 6 maandelijkse snapshots; object locks worden verlengd"

# --- 7. back-upjob -----------------------------------------------------------
step "7/9 Back-upjob (elke nacht ${BACKUP_TIME})"
install -d -m 0700 "$DUMP_DIR"; install -d -m 0755 "$STATUS_DIR"
cat > /usr/local/sbin/translog-backup <<EOF
#!/usr/bin/env bash
# Nachtelijke back-up TransLog app01 -> backup01 (Kopia, object lock)
set -euo pipefail
source ${CRED_FILE}
export KOPIA_PASSWORD="\$KOPIA_REPO_PASSWORD"
START=\$(date +%s)
echo "\$START" > ${STATUS_DIR}/last-start
sudo -u postgres pg_dump -Fc translog > ${DUMP_DIR}/translog.dump.tmp
mv ${DUMP_DIR}/translog.dump.tmp ${DUMP_DIR}/translog.dump
cp /etc/translog-portal.env /etc/samba/smb.conf ${DUMP_DIR}/
kopia snapshot create ${DUMP_DIR} ${SHARE_DIR} ${PORTAL_DIR} /etc/nginx
kopia maintenance run >/dev/null 2>&1 || true
END=\$(date +%s)
echo "\$END" > ${STATUS_DIR}/last-success
echo \$((END - START)) > ${STATUS_DIR}/last-duration-seconds
echo "Back-up geslaagd in \$((END - START)) seconden"
EOF
chmod 700 /usr/local/sbin/translog-backup
cat > /etc/systemd/system/translog-backup.service <<EOF
[Unit]
Description=TransLog nachtelijke back-up naar backup01
After=network-online.target postgresql.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/translog-backup
ExecStopPost=/bin/sh -c 'echo \$SERVICE_RESULT > ${STATUS_DIR}/last-result'
EOF
cat > /etc/systemd/system/translog-backup.timer <<EOF
[Unit]
Description=TransLog back-up elke nacht om ${BACKUP_TIME}

[Timer]
OnCalendar=*-*-* ${BACKUP_TIME}:00
Persistent=true
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now translog-backup.timer >/dev/null 2>&1
echo "  Eerste back-up maken..."
systemctl start translog-backup.service \
  && ok "Eerste back-up geslaagd in $(cat ${STATUS_DIR}/last-duration-seconds) seconden" \
  || fail "Eerste back-up mislukt. Zie: journalctl -u translog-backup -n 50"
ok "Timer actief, volgende run: $(systemctl show translog-backup.timer -p NextElapseUSecRealtime --value)"

# --- 8. hersteltest ----------------------------------------------------------
step "8/9 Hersteltest-script"
cat > /usr/local/sbin/translog-restore-test <<EOF
#!/usr/bin/env bash
# Zet de laatste snapshot terug in een testmap en testdatabase en vergelijkt.
# Raakt de productie NIET aan.
set -euo pipefail
source ${CRED_FILE}
export KOPIA_PASSWORD="\$KOPIA_REPO_PASSWORD"
T=/var/tmp/restoretest; rm -rf "\$T"; mkdir -p "\$T"
START=\$(date +%s)
for SRC in ${DUMP_DIR} ${SHARE_DIR}; do
  SNAP=\$(kopia snapshot list "\$SRC" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)[-1]["id"])')
  kopia snapshot restore "\$SNAP" "\$T\$SRC" >/dev/null
done
sudo -u postgres dropdb --if-exists translog_restoretest
sudo -u postgres createdb translog_restoretest
sudo -u postgres pg_restore -d translog_restoretest --no-owner < "\$T${DUMP_DIR}/translog.dump"
P=\$(sudo -u postgres psql -qtAd translog -c 'SELECT count(*) FROM zendingen')
R=\$(sudo -u postgres psql -qtAd translog_restoretest -c 'SELECT count(*) FROM zendingen')
FP=\$(find ${SHARE_DIR} -type f | wc -l); FR=\$(find "\$T${SHARE_DIR}" -type f | wc -l)
END=\$(date +%s)
echo "Zendingen: productie=\$P  hersteld=\$R"
echo "Bestanden: productie=\$FP  hersteld=\$FR"
echo "Hersteltijd: \$((END - START)) seconden"
if [ "\$R" -gt 0 ] && [ "\$FR" -gt 0 ]; then echo "HERSTELTEST GESLAAGD"; echo "\$END" > ${STATUS_DIR}/last-restore-test-ok; else echo "HERSTELTEST MISLUKT"; exit 1; fi
sudo -u postgres dropdb translog_restoretest; rm -rf "\$T"
EOF
chmod 700 /usr/local/sbin/translog-restore-test
/usr/local/sbin/translog-restore-test && ok "Hersteltest geslaagd" || warn "Hersteltest mislukt (zie uitvoer hierboven)"

# --- 9. ssh en firewall ------------------------------------------------------
step "9/9 SSH en host-firewall"
HOME_DIR=$(getent passwd "$SSH_USER" | cut -d: -f6)
if [ -f /tmp/key.pub ]; then
  install -d -m 700 -o "$SSH_USER" -g "$SSH_USER" "$HOME_DIR/.ssh"
  touch "$HOME_DIR/.ssh/authorized_keys"
  grep -qxF "$(cat /tmp/key.pub)" "$HOME_DIR/.ssh/authorized_keys" || cat /tmp/key.pub >> "$HOME_DIR/.ssh/authorized_keys"
  chown "$SSH_USER:$SSH_USER" "$HOME_DIR/.ssh/authorized_keys"; chmod 600 "$HOME_DIR/.ssh/authorized_keys"
  rm -f /tmp/key.pub; ok "SSH-sleutel toegevoegd"
fi
if [ -s "$HOME_DIR/.ssh/authorized_keys" ]; then
  echo "PasswordAuthentication no" > /etc/ssh/sshd_config.d/99-translog.conf
  sshd -t && systemctl restart ssh; ok "Wachtwoord-login uit"
else
  warn "Geen SSH-sleutel gevonden - wachtwoord-login NIET uitgezet"
fi
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null; ufw default allow outgoing >/dev/null
ufw allow from "$MON_NET" to any port 22    proto tcp comment 'SSH beheer'      >/dev/null
ufw allow from "$VPN_NET" to any port 22    proto tcp comment 'SSH OpenVPN'     >/dev/null
ufw allow from "$VPC_NET" to any port 80    proto tcp comment 'Portaal VPC'     >/dev/null
ufw allow from "$VPN_NET" to any port 80    proto tcp comment 'Portaal OpenVPN' >/dev/null
ufw allow from "$VPC_NET" to any port 445   proto tcp comment 'Fileshare VPC'   >/dev/null
ufw allow from "$VPN_NET" to any port 445   proto tcp comment 'Fileshare OpenVPN' >/dev/null
ufw allow from "$MON_NET" to any port 10050 proto tcp comment 'Zabbix-agent'    >/dev/null
ufw --force enable >/dev/null
ok "ufw actief: SSH, portaal (80), fileshare (445) en Zabbix-agent (10050) alleen vanaf VPC/OpenVPN"

# --- samenvatting ------------------------------------------------------------
echo
echo "============================================================"
echo -e "  \e[32mKLAAR - app01 is ingericht\e[0m"
echo "  Portaal     : http://${APP_IP}/   (health: http://${APP_IP}/health)"
echo "  Fileshare   : \\\\${APP_IP}\\translog   (gebruiker: translog)"
echo "  Back-up     : elke nacht ${BACKUP_TIME} naar s3://${BUCKET}/${KOPIA_PREFIX} op backup01"
echo "  Handmatig   : sudo systemctl start translog-backup"
echo "  Hersteltest : sudo translog-restore-test"
echo "  Wachtwoorden: sudo cat ${CRED_FILE}   -> Bitwarden, NIET in de chat"
echo "                KOPIA_REPO_PASSWORD kwijt = geen herstel mogelijk!"
echo "  Log         : ${LOG}"
echo "============================================================"
