import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config


def build_s3_client_from_env():
    access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("S3_SECRET_ACCESS_KEY")
    region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("S3_REGION") or "us-east-1"
    endpoint_url = os.getenv("S3_ENDPOINT_URL") or None
    force_path = os.getenv("S3_FORCE_PATH_STYLE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    kwargs = {
        "service_name": "s3",
        "region_name": region,
        "endpoint_url": endpoint_url,
        "config": Config(s3={"addressing_style": "path" if force_path else "auto"}),
    }
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    return boto3.client(**kwargs)


def validate_s3_connectivity(write_check: bool = True) -> None:
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("Missing required S3_BUCKET environment variable")

    prefix = os.getenv("S3_PREFIX", "oracle-schema").strip("/")
    client = build_s3_client_from_env()
    client.head_bucket(Bucket=bucket)

    if write_check:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        object_key = f"{prefix}/connectivity-check/{ts}.txt" if prefix else f"connectivity-check/{ts}.txt"
        client.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=b"prefect s3 connectivity check",
            ContentType="text/plain",
        )
        client.delete_object(Bucket=bucket, Key=object_key)
