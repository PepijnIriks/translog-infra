# translog-infra

Infrastructure as Code voor het project **Ransomware-bestendige infrastructuur** (TransLog B.V., verzonnen
case) in Fontys EduCloud. Alles wat op de servers staat, komt uit deze repository. Een server opnieuw
opbouwen is: de VM opnieuw aanmaken met de user data uit `bootstrap/`. De rest gaat vanzelf (eis E7).

Er staan **geen wachtwoorden of sleutels** in deze repository. Elke server maakt zijn eigen geheimen aan.
app01 haalt zijn back-uplogin één keer op bij backup01 (zie "Geheimen").

## Opbouw

| Server | Netwerk (VPC `translog`) | IP | Wat erop draait |
|---|---|---|---|
| app01 | productie 10.20.1.0/24 | 10.20.1.10 | Klantportaal (Flask + PostgreSQL + nginx), fileshare (Samba) met testdata, Kopia (back-up elke nacht 02:00), testscript |
| backup01 | backup 10.20.3.0/24 | 10.20.3.10 | SeaweedFS S3-opslag met **object lock (COMPLIANCE, 14 dagen)** op de 50 GB-datadisk |
| mon01 | beheer 10.20.2.0/24 | 10.20.2.10 | Zabbix 7.0 (server, web, PostgreSQL); toegang via SSH en web |

Extra op app01: de **instance-back-up van EduCloud** (Zone-EHV-BestEffort-Policy, dagelijks). Samen met
backup01 vormt dat optie 5 uit het Plan van Aanpak.

```
            EduCloud-VPN / Fontys-netwerk
                        |
   10.53.15.8 -> mon01 (:22 SSH, :80 Zabbix)  |  10.1.51.90 -> app01 (:80 portaal)
           |                 |        |
   +-------v-------+         |  +-----v--------+   S3 :8333 (alleen app01)   +----------------+
   | mon01 beheer  |-- SSH ---+->| app01        |---------------------------->| backup01       |
   | Zabbix        |<--agents----| productie    |   overdracht :8334 (1x)     | SeaweedFS      |
   +---------------+             +--------------+                             | object lock 14d|
                                  instance-back-up (EduCloud)                 +----------------+
```

Netwerkregels: de EduCloud-ACL's (`acl-productie`, `acl-beheer`, `acl-backup`) laten alleen het nodige
verkeer door. Op elke server staat daarnaast een eigen firewall (ufw, rol `common` en `host_vars/`).

## Hoe het werkt

1. In EduCloud maak je een VM met de juiste template, het netwerk, het vaste IP en de **user data** uit
   `bootstrap/userdata-<server>.yaml`.
2. cloud-init installeert git en Ansible, haalt deze repository op en draait `bootstrap/run.sh`.
3. `run.sh` past `ansible/site.yml` toe op de server zelf (`--limit <hostnaam>`).
4. Daarna past elke server de nieuwste versie van deze repository elke 30 minuten opnieuw toe
   (`translog-pull.timer`). Een wijziging in Git komt zo vanzelf op de servers.

Volgorde bij een volledige opbouw: **backup01 → mon01 → app01**. app01 wacht zelf tot backup01 klaar is.

## Geheimen

| Geheim | Gemaakt op | Staat in |
|---|---|---|
| S3-login admin (bucket beheren) | backup01 | `/etc/seaweedfs/secrets.env` (verlaat backup01 nooit) |
| S3-login kopia + Kopia-repositorywachtwoord | backup01 | backup01 + één keer naar app01 (`/etc/translog/backup.json`) |
| Database portaal | app01 | `/etc/translog/portal.env` |
| Zabbix-database en Admin-wachtwoord | mon01 | `/etc/zabbix/db.env`, `/root/zabbix-admin.txt` |

De overdracht naar app01 gaat via `translog-handoff` op backup01 (poort 8334, alleen vanaf het
productienetwerk, één verzoek, daarna stopt hij). Na een herbouw van app01 zet je hem op backup01 weer aan
met `sudo translog-handoff`.

## Eisen (Plan van Aanpak, tabel 2) en waar ze zitten

| Eis | Waar |
|---|---|
| E1 back-ups 14 dagen niet te wissen | `roles/seaweedfs` (bucket met object lock, COMPLIANCE) + Kopia `--retention-mode COMPLIANCE`; testen met `translog-attack-sim --only backups` |
| E2 backup01 apart, alleen app01 | EduCloud-ACL `acl-backup` + ufw (`host_vars/backup01.yml`); kopia-account mag de bucket niet beheren |
| E3 versleuteld | Kopia versleutelt alles op app01 (AES-256-GCM) voordat het naar backup01 gaat |
| E4/E5 RTO 4 uur, RPO 24 uur | back-up elke nacht 02:00; herstel: `docs/HERSTEL.md` |
| E6 geen versleutelde bestanden terug | `translog-restore --before <tijd>` kiest het laatste herstelpunt vóór de aanval en controleert op `*.locked` |
| E7 herbouwen met code | deze repository + `bootstrap/` |
| E9 extra server | nieuwe regel in `inventory/hosts.ini` + user data; de agent meldt zich zelf aan in Zabbix |
| E10 opslag 80 % | Zabbix-template "Linux by Zabbix agent active" (bestandssysteem van backup01) |
| E11 mislukte back-up binnen 15 min | Zabbix-trigger "Back-up mislukt" (controle elke minuut) |
| E12 aanval binnen 5 min | Zabbix-trigger "Mogelijke ransomware" (> 50 gewijzigde bestanden per minuut) en "Versleutelde bestanden" |
| E13 stappenplannen | `docs/HERSTEL.md` |

## Testscript

`translog-attack-sim` op app01 doet twee aanvalstechnieken uit MITRE ATT&CK na, alleen op de testdata:
T1490 (back-ups proberen te wissen, te overschrijven en de object lock uit te zetten) en T1486 (bestanden in
de fileshare versleutelen). Alles komt met tijden in `/var/log/translog-attack.log`.

```
sudo translog-attack-sim --ik-weet-het-zeker
```
