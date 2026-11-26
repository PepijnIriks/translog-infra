#!/usr/bin/env python3
"""Test E1 (object lock): tries to destroy back-ups the way ransomware with stolen keys would (MITRE T1490),
with the Kopia key from app01 AND with the admin key of backup01. Every attack must FAIL.

  T1a  delete a locked version of a test object          (kopia key, admin key, admin + governance bypass)
  T1b  overwrite the test object -> new version, the old one stays
  T1c  delete a REAL Kopia back-up file (app01/kopia.repository, every version)
  T2a  kopia key changes the bucket's object lock setting
  T2b  kopia key shortens the lock of a version
  T2c  kopia key / admin key deletes the whole bucket
  T2d  turn versioning off (admin key)

Writes one small test object per run (it stays locked for the lock period, like every back-up).
Result on screen and in /var/log/translog-locktest.log. Exit code 0 = all attacks failed = E1 met.
Usage (root, on backup01): translog-locktest
"""
import datetime
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ENDPOINT = "http://10.20.3.10:8333"
BUCKET = "translog-backups"
KOPIA_PREFIX = "app01/"
LOG = "/var/log/translog-locktest.log"
lines = []
fails = 0


def out(tag, text):
    line = f"[{tag}] {text}"
    print(line)
    lines.append(line)


def blocked(name, func):
    """Runs an attack; it must be refused by the storage."""
    global fails
    try:
        func()
        out("FOUT", f"{name}: GELUKT - dit had geweigerd moeten worden!")
        fails += 1
    except ClientError as e:
        out("OK  ", f"{name}: geweigerd ({e.response['Error']['Code']})")


def secrets():
    s = {}
    with open("/etc/seaweedfs/secrets.env") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                s[k] = v
    return s


def client(key, secret):
    return boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id=key, aws_secret_access_key=secret,
                        region_name="us-east-1", config=Config(s3={"addressing_style": "path"}))


def main():
    global fails
    s = secrets()
    admin = client(s["ADMIN_KEY"], s["ADMIN_SECRET"])
    kopia = client(s["KOPIA_KEY"], s["KOPIA_SECRET"])
    now = datetime.datetime.now(datetime.timezone.utc)
    out("INFO", f"Test E1 object lock - {now:%Y-%m-%d %H:%M} UTC - bucket {BUCKET}")

    cfg = admin.get_object_lock_configuration(Bucket=BUCKET)["ObjectLockConfiguration"]
    r = cfg.get("Rule", {}).get("DefaultRetention", {})
    out("INFO", f"Instelling bucket: object lock {cfg.get('ObjectLockEnabled')}, {r.get('Mode')} {r.get('Days')} dagen, "
                f"versiebeheer {admin.get_bucket_versioning(Bucket=BUCKET).get('Status')}")

    # Test object, written with the Kopia key (like a back-up)
    key = f"locktest/{now:%Y%m%d-%H%M%S}.txt"
    vid = kopia.put_object(Bucket=BUCKET, Key=key, Body=b"TransLog test object lock\n").get("VersionId")
    h = admin.head_object(Bucket=BUCKET, Key=key, VersionId=vid)
    out("INFO", f"Testobject {key} geschreven met de Kopia-sleutel: lock {h.get('ObjectLockMode')} "
                f"tot {h.get('ObjectLockRetainUntilDate')}")
    if h.get("ObjectLockMode") != "COMPLIANCE":
        out("FOUT", "Testobject heeft geen COMPLIANCE-lock gekregen"); fails += 1

    # T1a
    blocked("T1a versie wissen met Kopia-sleutel", lambda: kopia.delete_object(Bucket=BUCKET, Key=key, VersionId=vid))
    blocked("T1a versie wissen met admin-sleutel", lambda: admin.delete_object(Bucket=BUCKET, Key=key, VersionId=vid))
    blocked("T1a versie wissen met admin-sleutel + governance-bypass",
            lambda: admin.delete_object(Bucket=BUCKET, Key=key, VersionId=vid, BypassGovernanceRetention=True))

    # T1b
    kopia.put_object(Bucket=BUCKET, Key=key, Body=b"VERSLEUTELD DOOR AANVALLER\n")
    old = admin.get_object(Bucket=BUCKET, Key=key, VersionId=vid)["Body"].read()
    if old == b"TransLog test object lock\n":
        out("OK  ", "T1b overschrijven maakt een nieuwe versie; de oude versie is nog heel en leesbaar")
    else:
        out("FOUT", "T1b de oude versie is veranderd"); fails += 1

    # T1c - real back-up files
    vs = admin.list_object_versions(Bucket=BUCKET, Prefix=KOPIA_PREFIX + "kopia.repository").get("Versions", [])
    if not vs:
        out("LET OP", "T1c geen Kopia-repository gevonden onder " + KOPIA_PREFIX + " (is er al een back-up gemaakt?)")
    for v in vs:
        blocked(f"T1c echt back-upbestand {v['Key']} wissen (admin)",
                lambda v=v: admin.delete_object(Bucket=BUCKET, Key=v["Key"], VersionId=v["VersionId"]))
    if vs:
        still = admin.list_object_versions(Bucket=BUCKET, Prefix=KOPIA_PREFIX + "kopia.repository").get("Versions", [])
        (out("OK  ", f"T1c alle {len(still)} versies van kopia.repository bestaan nog") if len(still) == len(vs)
         else out("FOUT", "T1c er zijn versies verdwenen"))

    # T2
    blocked("T2a lock-instelling bucket wijzigen (Kopia-sleutel)",
            lambda: kopia.put_object_lock_configuration(Bucket=BUCKET, ObjectLockConfiguration={
                "ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}}))
    blocked("T2b bewaartijd inkorten tot morgen (Kopia-sleutel)",
            lambda: kopia.put_object_retention(Bucket=BUCKET, Key=key, VersionId=vid, Retention={
                "Mode": "COMPLIANCE", "RetainUntilDate": now + datetime.timedelta(days=1)}))
    blocked("T2b bewaartijd inkorten tot morgen (admin-sleutel)",
            lambda: admin.put_object_retention(Bucket=BUCKET, Key=key, VersionId=vid, Retention={
                "Mode": "COMPLIANCE", "RetainUntilDate": now + datetime.timedelta(days=1)}))
    blocked("T2c hele bucket wissen (Kopia-sleutel)", lambda: kopia.delete_bucket(Bucket=BUCKET))
    blocked("T2c hele bucket wissen (admin-sleutel)", lambda: admin.delete_bucket(Bucket=BUCKET))
    try:
        admin.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Suspended"})
        st = admin.get_bucket_versioning(Bucket=BUCKET).get("Status")
        if st == "Enabled":
            out("OK  ", "T2d versiebeheer uitzetten: genegeerd, staat nog aan")
        else:
            out("FOUT", f"T2d versiebeheer staat nu op {st}"); fails += 1
            admin.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    except ClientError as e:
        out("OK  ", f"T2d versiebeheer uitzetten: geweigerd ({e.response['Error']['Code']})")

    out("UITSLAG", "E1 GEHAALD - geen enkele aanval op de back-ups is gelukt" if fails == 0
        else f"E1 NIET GEHAALD - {fails} aanval(len) gelukt")
    with open(LOG, "a") as f:
        f.write("\n".join(lines) + "\n\n")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
