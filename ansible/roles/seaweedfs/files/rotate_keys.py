#!/usr/bin/env python3
"""Replaces the S3 key of app01 ("kopia" account) after an attack, because the attacker has seen it.

  1. makes a new key and secret in /etc/seaweedfs/secrets.env (the admin key and the Kopia repository password
     stay: the repository password encrypts the existing back-ups, which are locked and can't be re-encrypted)
  2. applies it (Ansible writes the new s3.json and restarts SeaweedFS)
  3. checks that the OLD key no longer works and the new one does
  4. opens the one-time hand-over, so the rebuilt app01 can pick up the new key once

Usage (root, on backup01): translog-rotate-keys
"""
import pathlib
import secrets
import subprocess
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError

ENV = pathlib.Path("/etc/seaweedfs/secrets.env")
ENDPOINT = "http://10.20.3.10:8333"
BUCKET = "translog-backups"


def read():
    return dict(line.split("=", 1) for line in ENV.read_text().splitlines() if "=" in line)


def works(key, secret):
    s3 = boto3.client("s3", endpoint_url=ENDPOINT, aws_access_key_id=key, aws_secret_access_key=secret,
                      region_name="us-east-1", config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}))
    try:
        s3.list_objects_v2(Bucket=BUCKET, MaxKeys=1)
        return True
    except ClientError:
        return False


def main():
    old = read()
    new = dict(old, KOPIA_KEY="kopia" + secrets.token_hex(8), KOPIA_SECRET=secrets.token_hex(24))
    ENV.write_text("".join(f"{k}={v}\n" for k, v in new.items()))
    ENV.chmod(0o600)
    print(f"Nieuwe sleutel voor app01 gemaakt ({new['KOPIA_KEY'][:9]}…). Toepassen (±1 minuut)…", flush=True)
    with open("/var/log/translog-rotate.log", "a") as log:
        r = subprocess.run(["bash", "/opt/translog-infra/bootstrap/run.sh"], stdout=log, stderr=subprocess.STDOUT)
    if r.returncode != 0:
        print("FOUT: toepassen mislukt, zie /var/log/translog-rotate.log")
        return 1
    for _ in range(60):
        try:
            if works(new["KOPIA_KEY"], new["KOPIA_SECRET"]):
                break
        except EndpointConnectionError:
            pass
        time.sleep(2)
    old_ok = works(old["KOPIA_KEY"], old["KOPIA_SECRET"])
    new_ok = works(new["KOPIA_KEY"], new["KOPIA_SECRET"])
    print(f"Oude sleutel ({old['KOPIA_KEY'][:9]}…): {'WERKT NOG - FOUT' if old_ok else 'geweigerd (ongeldig gemaakt)'}")
    print(f"Nieuwe sleutel ({new['KOPIA_KEY'][:9]}…): {'werkt' if new_ok else 'WERKT NIET - FOUT'}")
    subprocess.run(["systemctl", "start", "translog-handoff"], check=False)
    print("Overdracht geopend: de nieuwe app01 kan de nieuwe sleutel één keer ophalen.")
    return 0 if (new_ok and not old_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
