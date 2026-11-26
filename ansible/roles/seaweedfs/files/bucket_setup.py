#!/usr/bin/env python3
"""Makes the back-up bucket with object lock on (only possible when the bucket is made) and sets a default
retention of COMPLIANCE for N days: nothing written to the bucket can be deleted or overwritten for N days,
not even with the admin account. Safe to run again: an existing bucket is checked, not changed.

Uses the admin S3 login from /etc/seaweedfs/secrets.env (that login never leaves backup01).
"""
import argparse
import sys
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError


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
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--days", type=int, required=True)
    a = ap.parse_args()
    s = secrets()
    s3 = boto3.client("s3", endpoint_url=a.endpoint, aws_access_key_id=s["ADMIN_KEY"],
                      aws_secret_access_key=s["ADMIN_SECRET"], region_name="us-east-1",
                      config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 2}))

    for _ in range(60):                       # SeaweedFS needs a moment after (re)starting
        try:
            names = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
            break
        except (EndpointConnectionError, ClientError):
            time.sleep(5)
    else:
        sys.exit("S3 not reachable")

    if a.bucket not in names:
        s3.create_bucket(Bucket=a.bucket, ObjectLockEnabledForBucket=True)
        print(f"created bucket {a.bucket} with object lock")

    if s3.get_bucket_versioning(Bucket=a.bucket).get("Status") != "Enabled":
        s3.put_bucket_versioning(Bucket=a.bucket, VersioningConfiguration={"Status": "Enabled"})
        print("set versioning Enabled")

    want = {"ObjectLockEnabled": "Enabled",
            "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": a.days}}}
    try:
        have = s3.get_object_lock_configuration(Bucket=a.bucket)["ObjectLockConfiguration"]
    except ClientError:
        have = {}
    if have != want:
        s3.put_object_lock_configuration(Bucket=a.bucket, ObjectLockConfiguration=want)
        print(f"set default retention COMPLIANCE {a.days} days")
    ver = s3.get_bucket_versioning(Bucket=a.bucket).get("Status")
    print(f"bucket {a.bucket}: versioning={ver}, object lock={s3.get_object_lock_configuration(Bucket=a.bucket)['ObjectLockConfiguration']}")


if __name__ == "__main__":
    main()
