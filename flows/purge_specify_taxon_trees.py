"""Prefect flow: purge all Specify ``Taxon`` trees (all disciplines).

Run ``purge_specify_staging_reset`` instead if you also need specimens and geography cleared.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger

from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_setup import setup_django
from flows.lib.specify_taxon_purge import purge_all_taxon_trees

REPORT_CATEGORY_PURGE_TAXON = "purge-specify-taxon-trees"


@flow(
    name="Purge Specify taxon trees",
    description="Destructive: delete all Taxon rows and TaxonTreeDef metadata for every discipline.",
)
def purge_specify_taxon_trees_flow(dry_run: bool = True) -> dict[str, Any]:
    logger = get_run_logger()
    setup_django()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    manifest: dict[str, Any] = {
        "flow": "purge_specify_taxon_trees",
        "timestamp_utc": ts,
        "dry_run": dry_run,
        "result": purge_all_taxon_trees(dry_run=dry_run),
    }
    s3_key = migration_report_s3_key(REPORT_CATEGORY_PURGE_TAXON, ts)
    uploaded = upload_migration_report_json_task(manifest, s3_key)
    manifest["uploaded"] = uploaded
    manifest["report_uploaded"] = bool(uploaded)
    return manifest
