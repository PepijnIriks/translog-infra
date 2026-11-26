#!/usr/bin/env python3
"""Puts the back-up bucket back to how it was at a moment BEFORE an attack.

An attacker with the Kopia key cannot delete or change locked versions, but can write NEW versions over the
back-up files ("encrypted by ransomware") or hide them with delete markers. Kopia always reads the newest
version, so after such an attack Kopia sees garbage. The old versions are still there (object lock), so:
for every file whose newest version or delete marker is newer than --before, this script makes the newest
version from before that moment the current one again (copy of that old version). Nothing is deleted.

  translog-s3-rollback --before "2026-11-26 13:00"            show what would change (nothing is changed)
  translog-s3-rollback --before "2026-11-26 13:00" --doe-het  really do it

Time is local time (Europe/Amsterdam). Run as root on backup01 (uses the admin login).
"""
import argparse
import datetime
import sys
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config


def secrets():
    s = {}
    with open("/etc/seaweedfs/secrets.env") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                s[k] = v
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", required=True, help='moment before the attack, e.g. "2026-11-26 13:00"')
    ap.add_argument("--prefix", default="app01/")
    ap.add_argument("--bucket", default="translog-backups")
    ap.add_argument("--endpoint", default="http://10.20.3.10:8333")
    ap.add_argument("--doe-het", action="store_true", help="really change the bucket")
    a = ap.parse_args()
    t = datetime.datetime.strptime(a.before, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/Amsterdam"))

    s = secrets()
    s3 = boto3.client("s3", endpoint_url=a.endpoint, aws_access_key_id=s["ADMIN_KEY"],
                      aws_secret_access_key=s["ADMIN_SECRET"], region_name="us-east-1",
                      config=Config(s3={"addressing_style": "path"}))

    per_key = {}
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=a.bucket, Prefix=a.prefix):
        for v in page.get("Versions", []):
            per_key.setdefault(v["Key"], []).append(("v", v["LastModified"], v["VersionId"]))
        for d in page.get("DeleteMarkers", []):
            per_key.setdefault(d["Key"], []).append(("d", d["LastModified"], d["VersionId"]))

    todo = []
    for key, items in per_key.items():
        items.sort(key=lambda x: x[1])
        newest = items[-1]
        if newest[1] <= t:
            continue                                    # nothing happened to this file after the moment
        before = [i for i in items if i[1] <= t]
        if not before or before[-1][0] == "d":
            continue                                    # did not exist (or was already gone) at that moment
        todo.append((key, before[-1][2], "overschreven" if newest[0] == "v" else "verborgen (delete marker)"))

    print(f"Moment: {t:%Y-%m-%d %H:%M %Z} - {len(per_key)} bestanden bekeken, {len(todo)} terug te zetten")
    for key, vid, why in todo[:15]:
        print(f"  {why:28} {key}")
    if len(todo) > 15:
        print(f"  ... en nog {len(todo) - 15}")
    if not a.doe_het:
        print("Niets veranderd. Start opnieuw met --doe-het om terug te zetten.")
        return
    for key, vid, _ in todo:
        s3.copy_object(Bucket=a.bucket, Key=key, CopySource={"Bucket": a.bucket, "Key": key, "VersionId": vid})
    print(f"Klaar: {len(todo)} bestanden staan weer zoals op {t:%Y-%m-%d %H:%M}. Er is niets gewist.")


if __name__ == "__main__":
    sys.exit(main())
