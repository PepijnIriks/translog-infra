# Stappenplannen herstel en incident (E13)

Gebaseerd op het ITIL-incidentproces: beoordelen, loskoppelen, inlichten, herstellen, evalueren.
Noteer bij elke stap de tijd: dat is de meting voor E4 (RTO 4 uur) en E5 (RPO 24 uur).

## 0. Melding in Zabbix

- "Mogelijke ransomware" of "Versleutelde bestanden gevonden" op app01: ga naar stap 1.
- "Back-up mislukt" of "Geen geslaagde back-up in 26 uur": zie stap 5.

## 1. Beoordelen en loskoppelen (doel: binnen 15 minuten)

1. Open http://10.1.51.90 (portaal). Zie je "bestanden versleuteld", dan is het een aanval.
2. Koppel app01 los: zet in EduCloud de ACL van het netwerk `productie` tijdelijk op `default_deny`,
   of stop app01 (Instances > app01 > Stop).
3. Licht in: IT-beheer, directie, security officer. Bij persoonsgegevens: privacy officer (AVG-meldplicht
   72 uur).
4. Noteer de **tijd van de aanval**: de eerste melding in Zabbix, of `/var/log/translog-attack.log` bij een test.

## 2. Snel herstel: instance-back-up (manier A)

1. EduCloud > Storage > Backups: kies de back-up van app01 van **vóór de aanval**.
2. "Restore backup", wacht tot app01 weer draait.
3. Controleer: http://10.1.51.90/health geeft `"ok": true` en `"versleuteld": 0`.
4. Data van na de back-up tot de aanval: zet die terug uit backup01 (stap 3, alleen stap 3 en stap 6).

## 3. Herstel zonder instance-back-up: opnieuw opbouwen (manier B, E7)

De veilige manier na een echte aanval: de aanvaller kan nog op app01 zitten, dus nooit terugzetten op de
oude server. Gebruik ook de instance-back-ups niet als niet zeker is dat ze van vóór de inbraak zijn.
De demo-website (knop "Realistische demo") doet deze stappen automatisch.

0. Op **backup01**: `sudo translog-rotate-keys` (nieuwe S3-sleutel voor app01, de oude wordt ongeldig,
   de overdracht gaat open). Doe dit vóór app01 terugkomt.

1. EduCloud > Instances > app01 > Destroy (Expunge aan).
2. Add Instance: Ubuntu 24.04, netwerk `productie`, IP **10.20.1.10**, naam `app01`, user data uit
   `bootstrap/userdata-app01.yaml`. Backup-offering weer koppelen.
3. Op **backup01**: zijn er back-upbestanden overschreven door de aanvaller? Kijk eerst:
   `sudo translog-s3-rollback --before "JJJJ-MM-DD UU:MM"` (tijd van vóór de aanval; verandert niets).
   Staan er bestanden in de lijst, zet ze terug met hetzelfde commando plus `--doe-het`. Er wordt niets
   gewist: de oude, vergrendelde versies worden weer de actuele.
4. Op **backup01**: `sudo translog-handoff`. app01 haalt zijn back-uplogin dan één keer op.
5. Wacht tot app01 klaar is (`/var/log/translog-bootstrap.log` op app01, ±10 minuten).
6. Op **app01**: `sudo translog-restore --list` en daarna
   `sudo translog-restore --before "JJJJ-MM-DD UU:MM"` met de tijd van de aanval.
7. Het script controleert zelf dat er geen `*.locked`-bestanden meer zijn en dat het portaal werkt.

## 4. Evalueren

Binnen een week: wat ging goed, wat kan beter (root cause analysis), en pas dit stappenplan en de
drempels in Zabbix aan. Leg de gemeten hersteltijd en het dataverlies vast.

## 5. Mislukte back-up

1. Op **app01**: `sudo tail -n 50 /var/log/translog-backup.log`.
2. Code 11 = databasedump mislukt; 12 = back-up naar backup01 mislukt (is backup01 bereikbaar?
   Zabbix "S3-opslag niet bereikbaar"); 13 = controle van de back-up mislukt.
3. Opnieuw proberen: `sudo systemctl start translog-backup`. De melding verdwijnt als de back-up lukt.

## Inloggen

- **mon01**: `ssh ubuntu@10.53.15.8` (VPN aan). Wachtwoord: wat EduCloud bij het aanmaken toonde.
- **app01 / backup01**: alleen vanaf mon01: `ssh ubuntu@10.20.1.10` of `ssh ubuntu@10.20.3.10`.
- **Zabbix**: http://10.53.15.8, gebruiker `Admin`, wachtwoord op mon01 in `/root/zabbix-admin.txt`.
