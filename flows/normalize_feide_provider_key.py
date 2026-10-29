"""Prefect flow: normalize spuserexternalid.provider to capital-F ``Feide`` on staging + test."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger, task

from flows.lib.mariadb_logical_sync import (
    staging_endpoint_from_env,
    test_endpoint_via_tunnel,
)
from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.oauth_provider_normalize import (
    CANONICAL_FEIDE_PROVIDER,
    normalize_feide_provider_key,
)
from flows.lib.ssh_tunnel import ssh_local_forward, ssh_tunnel_config_from_env

REPORT_CATEGORY = "normalize-feide-provider-key"


@task(name="normalize-feide-provider-key-run")
def normalize_feide_provider_key_task(*, dry_run: bool) -> dict[str, Any]:
    logger = get_run_logger()
    staging = staging_endpoint_from_env()
    tunnel_cfg = ssh_tunnel_config_from_env()

    staging_report = normalize_feide_provider_key(staging, dry_run=dry_run)
    logger.info("staging: %s", staging_report.as_dict())

    with ssh_local_forward(tunnel_cfg) as tun:
        test_ep = test_endpoint_via_tunnel(local_port=tun.local_port)
        test_report = normalize_feide_provider_key(test_ep, dry_run=dry_run)
        logger.info("test: %s", test_report.as_dict())

    return {
        "dry_run": dry_run,
        "canonical_provider": CANONICAL_FEIDE_PROVIDER,
        "staging": staging_report.as_dict(),
        "test": test_report.as_dict(),
    }


@flow(
    name="Normalize Feide Provider Key",
    description=(
        "Rename spuserexternalid.provider from lowercase feide (any case mismatch) "
        "to Feide on staging and test. Drops lowercase duplicates when a Feide row "
        "already exists for the same providerid. Default dry_run=true."
    ),
)
def normalize_feide_provider_key_flow(dry_run: bool = True) -> dict[str, Any]:
    logger = get_run_logger()
    logger.info("normalize_feide_provider_key_flow | dry_run=%s", dry_run)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = normalize_feide_provider_key_task(dry_run=dry_run)

    report = {
        "flow": "normalize_feide_provider_key",
        "ts": ts,
        **result,
    }
    s3_key = migration_report_s3_key(REPORT_CATEGORY, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info("Uploaded report: %s", uri)
    report["uploaded"] = uploaded
    return report
