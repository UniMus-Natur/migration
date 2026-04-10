"""Shared Prefect task: upload migration ``report.json`` to S3.

Upload runs in a **task** so it executes in the same worker process context as
other ``@task`` steps (Oracle/Django), which matches how successful flows like
``oracle_feide_field_profile`` perform S3 writes. The flow runner alone can lack
the worker's ``envFrom`` secret in some setups.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from prefect import get_run_logger, task

from flows.lib.s3_connectivity import upload_file_with_compat_retry


@task(retries=2, retry_delay_seconds=5)
def upload_migration_report_json_task(report: dict, object_key: str) -> list[str]:
    """Persist ``report`` as JSON and upload to ``S3_BUCKET`` at ``object_key``.

    Returns ``[\"s3://bucket/key\"]`` on success, or ``[]`` if ``S3_BUCKET`` is unset.
    """
    log = get_run_logger()
    bucket = (os.getenv("S3_BUCKET") or "").strip()
    if not bucket:
        # INFO so it shows in default Prefect log views that hide WARNING.
        log.info(
            "MIGRATION_REPORT_SKIP reason=no_S3_BUCKET — "
            "set S3_BUCKET (and S3 credentials) on the Prefect worker secret; "
            "flow results are otherwise unchanged."
        )
        return []

    uri = f"s3://{bucket}/{object_key}"
    uploaded: list[str] = []
    with tempfile.TemporaryDirectory(prefix="migration-report-") as tmp:
        path = Path(tmp) / "report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info(f"Uploading migration report to {uri}")
        upload_file_with_compat_retry(str(path), bucket, object_key)
        uploaded.append(uri)

    log.info(f"Migration report upload finished: {uri}")
    return uploaded
