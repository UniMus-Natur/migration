"""Prefect flow: sync staging Specify MariaDB → test via SSH LocalForward."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger, task

from flows.lib.mariadb_logical_sync import (
    assert_compatible,
    assert_not_same_endpoint,
    build_compatibility_report,
    compatibility_report_dict,
    staging_endpoint_from_env,
    stream_dump_restore,
    test_endpoint_via_tunnel,
)
from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.ssh_tunnel import ssh_local_forward, ssh_tunnel_config_from_env

REPORT_CATEGORY = "sync-specify-db-to-test"


@task(name="sync-specify-db-to-test-run")
def sync_specify_db_to_test_task(*, dry_run: bool, force: bool = False) -> dict[str, Any]:
    logger = get_run_logger()
    source = staging_endpoint_from_env()
    tunnel_cfg = ssh_tunnel_config_from_env()
    assert_not_same_endpoint(
        source,
        test_remote_host=tunnel_cfg.remote_host,
        test_remote_port=tunnel_cfg.remote_port,
        test_db_name=(os.environ.get("TEST_DB_NAME") or "").strip(),
    )

    out: dict[str, Any] = {
        "dry_run": dry_run,
        "force": force,
        "source": {
            "host": source.host,
            "port": source.port,
            "database": source.database,
            "user": source.user,
        },
        "test_remote": {
            "host": tunnel_cfg.remote_host,
            "port": tunnel_cfg.remote_port,
            "bastion": f"{tunnel_cfg.bastion_user}@{tunnel_cfg.bastion_host}:{tunnel_cfg.bastion_port}",
            "local_forward_port": tunnel_cfg.local_port,
        },
    }

    with ssh_local_forward(tunnel_cfg) as tun:
        target = test_endpoint_via_tunnel(local_port=tun.local_port)
        report = build_compatibility_report(source, target)
        out["compatibility"] = compatibility_report_dict(report)
        logger.info(
            "compatibility versions_match=%s schemas_match=%s force=%s "
            "source_tables=%s target_tables=%s source_bytes≈%s",
            report.versions_match,
            report.schemas_match,
            force,
            report.source_table_count,
            report.target_table_count,
            report.source_approx_data_bytes,
        )
        # force only bypasses the pre-restore gate (wipe-and-replace of mismatched DDL).
        assert_compatible(report, force=force)

        if dry_run:
            if report.schemas_match:
                out["message"] = (
                    "dry_run: compatibility OK; would stream mysqldump from staging "
                    "into test over SSH tunnel (destructive replace of test database objects)"
                )
            else:
                out["message"] = (
                    "dry_run: schema fingerprints differ; force=True would stream "
                    "mysqldump and replace test DDL/data with staging"
                )
            return out

        restore = stream_dump_restore(source=source, target=target)
        out["restore"] = restore
        # After restore, fingerprints must match even if force was used.
        post = build_compatibility_report(source, target)
        out["post_restore_compatibility"] = compatibility_report_dict(post)
        assert_compatible(post)
        out["message"] = "sync completed"
        return out


@flow(name="Sync Specify DB to Test")
def sync_specify_db_to_test_flow(dry_run: bool = True, force: bool = False) -> dict[str, Any]:
    """Full logical dump of staging Specify DB into test through an SSH bastion tunnel.

    Hard-fails unless ``information_schema`` column fingerprints match, unless
    ``force=True`` (allows first wipe-and-replace when target DDL was bootstrapped
    differently). Post-restore fingerprint check is never skipped.
    ``spversion`` is reported for diagnostics but not required on the target.
    Default ``dry_run=True`` only opens the tunnel and runs the gate.
    """
    logger = get_run_logger()
    logger.info("sync_specify_db_to_test_flow | dry_run=%s force=%s", dry_run, force)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = sync_specify_db_to_test_task(dry_run=dry_run, force=force)
    logger.warning("sync_specify_db_to_test result=%s", result)

    report = {
        "flow": "sync_specify_db_to_test",
        "ts": ts,
        "dry_run": dry_run,
        "force": force,
        "result": result,
    }
    s3_key = migration_report_s3_key(REPORT_CATEGORY, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info("Uploaded report: %s", uri)
    return report
