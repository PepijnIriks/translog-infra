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

### backup01 inrichten

Op **backup01**:

```
wget -O /tmp/setup-backup01.sh https://raw.githubusercontent.com/PepijnIriks/translog-infra/main/setup-backup01.sh
sudo bash /tmp/setup-backup01.sh
```

Er staan geen geheimen in deze repo. S3-sleutels worden op de server zelf gemaakt en staan in `/root/seaweedfs-credentials.txt`.
