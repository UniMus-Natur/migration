"""Prefect flow: reset a Specify staging DB — wipe specimen, geography, and taxon data.

**Keeps:** ``Agent``, ``SpecifyUser``, institution/collection/discipline structure.

**Removes:** all collection objects (every collection), geography + locality, all taxon trees,
and migration bridge tables (``migration_oracle_objectmap``, ``migration_oracle_placemap``).

Use ``dry_run=True`` first. This is destructive and intended for migration/staging only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger

from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_setup import setup_django
from flows.lib.specify_taxon_purge import purge_all_taxon_trees
from flows.purge_specify_dataset import _purge_dataset
from flows.purge_specify_geography_locality import _purge_localities_and_geographies

REPORT_CATEGORY_STAGING_RESET = "purge-specify-staging-reset"


def _purge_all_collections(
    *,
    dry_run: bool,
    clear_objectmap_rows: bool,
    clear_placemap_rows: bool,
    logger: Any,
) -> list[dict[str, Any]]:
    from specifyweb.specify.models import Collection

    results: list[dict[str, Any]] = []
    for coll in Collection.objects.order_by("code"):
        code = (coll.code or "").strip()
        if not code:
            continue
        logger.warning("purge staging: dataset collection=%s", code)
        results.append(
            _purge_dataset(
                collection_code=code,
                dry_run=dry_run,
                clear_objectmap_rows=clear_objectmap_rows,
                clear_placemap_rows=clear_placemap_rows,
                logger=logger,
            )
        )
    return results


@flow(
    name="Purge Specify staging reset",
    description=(
        "Destructive: delete all specimens, geography/locality, and taxon trees. "
        "Keeps users and agents."
    ),
)
def purge_specify_staging_reset_flow(
    dry_run: bool = True,
    clear_objectmap_rows: bool = True,
    clear_placemap_rows: bool = True,
    purge_taxon_trees: bool = True,
    purge_geography_locality: bool = True,
    purge_all_collection_data: bool = True,
) -> dict[str, Any]:
    """Full staging cleanup except ``Agent`` and ``SpecifyUser``."""
    logger = get_run_logger()
    setup_django()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    manifest: dict[str, Any] = {
        "flow": "purge_specify_staging_reset",
        "timestamp_utc": ts,
        "dry_run": dry_run,
        "keeps": ["Agent", "SpecifyUser", "Collection", "Discipline", "Division", "Institution"],
    }

    if purge_all_collection_data:
        manifest["collections"] = _purge_all_collections(
            dry_run=dry_run,
            clear_objectmap_rows=clear_objectmap_rows,
            clear_placemap_rows=clear_placemap_rows,
            logger=logger,
        )

    if purge_geography_locality:
        manifest["geography_locality"] = _purge_localities_and_geographies(
            dry_run=dry_run,
            clear_placemap_rows=clear_placemap_rows,
            logger=logger,
        )

    if purge_taxon_trees:
        manifest["taxon_trees"] = purge_all_taxon_trees(dry_run=dry_run)

    manifest["message"] = "staging reset complete" if not dry_run else "dry_run staging reset preview"

    s3_key = migration_report_s3_key(REPORT_CATEGORY_STAGING_RESET, ts)
    uploaded = upload_migration_report_json_task(manifest, s3_key)
    manifest["uploaded"] = uploaded
    manifest["report_uploaded"] = bool(uploaded)
    return manifest
