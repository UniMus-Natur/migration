import os
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_s3_client_from_env(payload_signing_enabled: bool | None = None):
    access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("S3_SECRET_ACCESS_KEY")
    region = os.getenv("AWS_DEFAULT_REGION") or os.getenv("S3_REGION") or "us-east-1"
    endpoint_url = os.getenv("S3_ENDPOINT_URL") or None
    force_path = _env_bool("S3_FORCE_PATH_STYLE", True)
    payload_signing_enabled = (
        _env_bool("S3_PAYLOAD_SIGNING_ENABLED", False)
        if payload_signing_enabled is None
        else payload_signing_enabled
    )

    kwargs = {
        "service_name": "s3",
        "region_name": region,
        "endpoint_url": endpoint_url,
        "config": Config(
            signature_version="s3v4",
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            s3={
                "addressing_style": "path" if force_path else "auto",
                "payload_signing_enabled": payload_signing_enabled,
            },
        ),
    }
    if access_key and secret_key:
        kwargs["aws_access_key_id"] = access_key
        kwargs["aws_secret_access_key"] = secret_key

    return boto3.client(**kwargs)


def _iter_payload_signing_attempts() -> list[bool]:
    first = _env_bool("S3_PAYLOAD_SIGNING_ENABLED", False)
    second = not first
    return [first] if first == second else [first, second]


def upload_file_with_compat_retry(local_path: str, bucket: str, key: str) -> None:
    last_exc = None
    for payload_signing_enabled in _iter_payload_signing_attempts():
        client = build_s3_client_from_env(payload_signing_enabled=payload_signing_enabled)
        try:
            client.upload_file(local_path, bucket, key)
            return
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "XAmzContentSHA256Mismatch":
                raise
            last_exc = exc
    if last_exc:
        raise last_exc


def validate_s3_connectivity(write_check: bool = True) -> None:
    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("Missing required S3_BUCKET environment variable")

    prefix = os.getenv("S3_PREFIX", "oracle-schema").strip("/")
    head_exc = None
    working_client = None
    for payload_signing_enabled in _iter_payload_signing_attempts():
        client = build_s3_client_from_env(payload_signing_enabled=payload_signing_enabled)
        try:
            client.head_bucket(Bucket=bucket)
            working_client = client
            break
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "XAmzContentSHA256Mismatch":
                raise
            head_exc = exc
    if not working_client:
        raise head_exc if head_exc else RuntimeError("Unable to initialize S3 client")

    if write_check:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        object_key = f"{prefix}/connectivity-check/{ts}.txt" if prefix else f"connectivity-check/{ts}.txt"
        put_exc = None
        for payload_signing_enabled in _iter_payload_signing_attempts():
            client = build_s3_client_from_env(payload_signing_enabled=payload_signing_enabled)
            try:
                client.put_object(
                    Bucket=bucket,
                    Key=object_key,
                    Body=b"prefect s3 connectivity check",
                    ContentType="text/plain",
                )
                client.delete_object(Bucket=bucket, Key=object_key)
                return
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") != "XAmzContentSHA256Mismatch":
                    raise
                put_exc = exc
        if put_exc:
            raise put_exc
