#!/usr/bin/env python3
"""Shows what is in the back-up bucket: every object version, its size, its object lock (mode + until when)
and whether it can be read back. Read-only: changes nothing. Uses the admin login from /etc/seaweedfs/secrets.env.

Usage (root, on backup01):  translog-s3-list [--prefix app01/] [--max 40]
"""
import argparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def secrets():
    out = {}
    with open("/etc/seaweedfs/secrets.env") as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="http://10.20.3.10:8333")
    ap.add_argument("--bucket", default="translog-backups")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--max", type=int, default=40)
    a = ap.parse_args()
    s = secrets()
    s3 = boto3.client("s3", endpoint_url=a.endpoint, aws_access_key_id=s["ADMIN_KEY"],
                      aws_secret_access_key=s["ADMIN_SECRET"], region_name="us-east-1",
                      config=Config(s3={"addressing_style": "path"}))

    print("== bucket", a.bucket)
    print("versioning :", s3.get_bucket_versioning(Bucket=a.bucket).get("Status"))
    try:
        lock = s3.get_object_lock_configuration(Bucket=a.bucket)["ObjectLockConfiguration"]
        print("object lock:", lock)
    except ClientError as e:
        print("object lock: ERROR", e.response["Error"]["Code"])

    print("\n== list_objects_v2 (what Kopia sees), prefix", repr(a.prefix))
    r = s3.list_objects_v2(Bucket=a.bucket, Prefix=a.prefix, MaxKeys=a.max)
    for o in r.get("Contents", []):
        try:
            s3.head_object(Bucket=a.bucket, Key=o["Key"])
            ok = "readable"
        except ClientError as e:
            ok = "NOT READABLE " + e.response["Error"]["Code"]
        print(f'{o["Size"]:>10}  {o["Key"]}  {ok}')
    print("total listed:", r.get("KeyCount"), "(more)" if r.get("IsTruncated") else "")

    print("\n== versions + locks, prefix", repr(a.prefix))
    v = s3.list_object_versions(Bucket=a.bucket, Prefix=a.prefix, MaxKeys=a.max)
    for o in v.get("Versions", []):
        try:
            ret = s3.get_object_retention(Bucket=a.bucket, Key=o["Key"], VersionId=o["VersionId"])["Retention"]
            lk = f'{ret["Mode"]} until {ret["RetainUntilDate"]:%Y-%m-%d %H:%M}'
        except ClientError as e:
            lk = "no lock (" + e.response["Error"]["Code"] + ")"
        print(f'{"latest" if o["IsLatest"] else "old   "}  {o["Key"]}  v={o["VersionId"][:12]}  {lk}')
    for d in v.get("DeleteMarkers", []):
        print(f'DELETE-MARKER  {d["Key"]}')


if __name__ == "__main__":
    main()
