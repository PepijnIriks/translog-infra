# translog-infra

Infrastructuurcode voor het individuele project **Ransomware-bestendige infrastructuur** (TransLog B.V., Fontys EduCloud).

Alle code staat hier, buiten de productieserver app01, zodat de omgeving ook na een ransomware-aanval opnieuw kan worden opgebouwd (eis E7/E11 uit het Plan van Aanpak).

## Servers (VPC `translog`)

| Server | Tier | IP | Rol |
|---|---|---|---|
| app01 | productie | 10.20.1.254 | Portaal, PostgreSQL, fileshare, Kopia |
| backup01 | backup | 10.20.3.198 | SeaweedFS S3 met object lock |
| mon01 | beheer | 10.20.2.238 | Zabbix |

## Scripts

| Script | Server | Wat het doet |
|---|---|---|
| `setup-backup01.sh` | backup01 | SeaweedFS installeren, S3-sleutels (admin/kopia), bucket `translog-backups` met object lock (COMPLIANCE, 14 dagen), zelftest T1/T2, SSH-hardening en ufw |
| `setup-app01.sh` | app01 | PostgreSQL met testdata, portaal "zendingen volgen" (Flask + nginx), Samba-fileshare, Kopia-repository met object lock, nachtelijke back-up 01:00, hersteltest, ufw |

### backup01 inrichten

Op **backup01**:

```
wget -O /tmp/setup-backup01.sh https://raw.githubusercontent.com/PepijnIriks/translog-infra/main/setup-backup01.sh
sudo bash /tmp/setup-backup01.sh
```

### app01 inrichten

Eerst backup01, daarna op **app01**:

```
wget -O /tmp/setup-app01.sh https://raw.githubusercontent.com/PepijnIriks/translog-infra/main/setup-app01.sh
sudo bash /tmp/setup-app01.sh
```

Het script vraagt de Kopia-sleutel van backup01. Daarna:

- handmatige back-up: `sudo systemctl start translog-backup`
- hersteltest: `sudo translog-restore-test`

Er staan geen geheimen in deze repo. S3-sleutels worden op de server zelf gemaakt en staan in `/root/seaweedfs-credentials.txt`.
