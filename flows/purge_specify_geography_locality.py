"""Prefect flow: purge Specify ``Locality`` and ``Geography`` data.

Use this only in migration/staging environments where destroying these tables is intended.
The flow is idempotent and supports ``dry_run``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger

from flows.lib.migration_oracle_placemap import TABLE_NAME as PLACEMAP_TABLE
from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_setup import setup_django


def _purge_localities_and_geographies(
    *,
    dry_run: bool,
    clear_placemap_rows: bool,
    logger: Any,
) -> dict[str, Any]:
    """Clear Locality + Geography with direct SQL (fast destructive maintenance)."""
    from django.db import connection

    out: dict[str, Any] = {
        "dry_run": dry_run,
        "clear_placemap_rows": clear_placemap_rows,
        "collectingevents_with_locality_before": 0,
        "locality_count_before": 0,
        "agentgeography_count_before": 0,
        "geography_count_before": 0,
        "collectingevents_locality_nulled": 0,
        "locality_count_after": 0,
        "agentgeography_count_after": 0,
        "geography_count_after": 0,
        "placemap_count_before": 0,
        "placemap_count_after": 0,
        "tables_truncated": [],
    }

    def _table_exists(cur: Any, table: str) -> bool:
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", [table])
        row = cur.fetchone()
        return bool(row and int(row[0]) > 0)

    def _count(cur: Any, sql: str) -> int:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] if row and row[0] is not None else 0)

    with connection.cursor() as cur:
        out["collectingevents_with_locality_before"] = _count(
            cur, "SELECT COUNT(*) FROM collectingevent WHERE LocalityID IS NOT NULL"
        )
        out["locality_count_before"] = _count(cur, "SELECT COUNT(*) FROM locality")
        out["agentgeography_count_before"] = _count(cur, "SELECT COUNT(*) FROM agentgeography")
        out["geography_count_before"] = _count(cur, "SELECT COUNT(*) FROM geography")
        if clear_placemap_rows and _table_exists(cur, PLACEMAP_TABLE):
            out["placemap_count_before"] = _count(cur, f"SELECT COUNT(*) FROM {PLACEMAP_TABLE}")

    if dry_run:
        out["message"] = (
            "dry_run: would null CollectingEvent.locality and TRUNCATE locality/geography/agentgeography "
            "(plus locality child tables); optional placemap clear"
        )
        return out

    # Order matters: clear CollectingEvent FK first, then fast truncates under FK_CHECKS=0.
    truncate_candidates = [
        "localityattachment",
        "localitycitation",
        "localitydetail",
        "localitynamealias",
        "geocoorddetail",
        "latlonpolygon",
        "agentgeography",
        "locality",
        "geography",
    ]
    with connection.cursor() as cur:
        cur.execute("UPDATE collectingevent SET LocalityID = NULL WHERE LocalityID IS NOT NULL")
        out["collectingevents_locality_nulled"] = int(cur.rowcount if cur.rowcount is not None else 0)

        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            for table in truncate_candidates:
                if _table_exists(cur, table):
                    cur.execute(f"TRUNCATE TABLE {table}")
                    out["tables_truncated"].append(table)
            if clear_placemap_rows and _table_exists(cur, PLACEMAP_TABLE):
                cur.execute(f"TRUNCATE TABLE {PLACEMAP_TABLE}")
                out["tables_truncated"].append(PLACEMAP_TABLE)
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")

        out["locality_count_after"] = _count(cur, "SELECT COUNT(*) FROM locality")
        out["agentgeography_count_after"] = _count(cur, "SELECT COUNT(*) FROM agentgeography")
        out["geography_count_after"] = _count(cur, "SELECT COUNT(*) FROM geography")
        if clear_placemap_rows and _table_exists(cur, PLACEMAP_TABLE):
            out["placemap_count_after"] = _count(cur, f"SELECT COUNT(*) FROM {PLACEMAP_TABLE}")

    out["message"] = "purge complete"
    logger.warning(
        "purge_specify_geography_locality done ce_nulled=%s locality_after=%s geography_after=%s truncated=%s",
        out["collectingevents_locality_nulled"],
        out["locality_count_after"],
        out["geography_count_after"],
        out["tables_truncated"],
    )
    return out


@flow(
    name="Purge Specify geography+locality",
    description=(
        "Destructive maintenance flow: null CollectingEvent.locality, delete all Locality + Geography "
        "rows (ORM), optionally clear migration_oracle_placemap rows; uploads S3 report."
    ),
)
def purge_specify_geography_locality_flow(
    dry_run: bool = True,
    clear_placemap_rows: bool = True,
) -> dict[str, Any]:
    """Delete all Specify Geography and Locality rows with FK-safe ordering."""
    logger = get_run_logger()
    setup_django()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    manifest: dict[str, Any] = {
        "flow": "purge_specify_geography_locality",
        "timestamp_utc": ts,
        "dry_run": dry_run,
        "clear_placemap_rows": clear_placemap_rows,
    }
    logger.warning(
        "purge_specify_geography_locality start dry_run=%s clear_placemap_rows=%s (direct SQL mode)",
        dry_run,
        clear_placemap_rows,
    )
    manifest["result"] = _purge_localities_and_geographies(
        dry_run=dry_run,
        clear_placemap_rows=clear_placemap_rows,
        logger=logger,
    )
    logger.warning("purge_specify_geography_locality result=%s", manifest["result"])

    s3_key = migration_report_s3_key(REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY, ts)
    uploaded = upload_migration_report_json_task(manifest, s3_key)
    manifest["uploaded"] = uploaded
    manifest["report_uploaded"] = bool(uploaded)
    return manifest

