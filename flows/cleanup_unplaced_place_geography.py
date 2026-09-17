"""Prefect flow: re-point localities off legacy ``hpo:place*`` geography placeholders onto Earth."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger, task

from flows.lib.cleanup_unplaced_geography import cleanup_unplaced_place_geography_placeholders
from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_setup import setup_django

REPORT_CATEGORY = "cleanup-unplaced-geography"


@task(name="cleanup-unplaced-place-geography")
def cleanup_unplaced_place_geography_task(
    dry_run: bool,
    geography_treedef_id: int | None,
) -> dict[str, Any]:
    return cleanup_unplaced_place_geography_placeholders(
        dry_run=dry_run,
        geography_treedef_id=geography_treedef_id,
    )


@flow(name="Cleanup Unplaced Place Geography")
def cleanup_unplaced_place_geography_flow(
    dry_run: bool = True,
    geography_treedef_id: int | None = None,
) -> dict[str, Any]:
    """Move Locality/placemap off invented Continent placeholders; delete those Geography rows.

    Default ``dry_run=True``. Pass ``dry_run=false`` only after reviewing the dry-run counts.
    """
    logger = get_run_logger()
    logger.info(
        "cleanup_unplaced_place_geography_flow | dry_run=%s geography_treedef_id=%s",
        dry_run,
        geography_treedef_id,
    )
    setup_django()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    result = cleanup_unplaced_place_geography_task(dry_run, geography_treedef_id)
    logger.warning("cleanup_unplaced_place_geography result=%s", result)

    report = {
        "flow": "cleanup_unplaced_place_geography",
        "ts": ts,
        "dry_run": dry_run,
        "geography_treedef_id": geography_treedef_id,
        "result": result,
    }
    s3_key = migration_report_s3_key(REPORT_CATEGORY, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info("Uploaded report: %s", uri)
    return report
