"""Prefect flow: migrate Oslo vascular plants herbarium to Specify 7.

Source
------
    Oracle ``MUSIT_BOTANIKK_FELLES.V_OBJECT_ATTRIBUTES``
    filtered by ``institutioncode = 'O'`` and ``collectioncode = 'V'``.
    Approximately 1,149,083 objects in PROD snapshot.

Target
------
    Specify 7 collection ``O-V`` (discipline ``Karplanter Moser``).
    Writes: ``CollectingEvent``, ``Locality`` (on-the-fly), ``CollectionObject``,
    ``Determination``.  Never creates or modifies ``Agent``, ``Geography``,
    ``Taxon``, or ``SpecifyUser``.

Idempotency
-----------
    Before each object is migrated, the loader checks ``migration_oracle_objectmap``
    and the deterministic CollectionObject GUID; already-present objects are skipped.
    Re-run without ``purge_before_run`` to resume after OOM/cancel.

Test run
--------
    Set ``limit=100`` to migrate only 100 records.  The flow logs an estimated time
    for the full migration based on observed throughput.

    Set ``only_catalog="O-V-398038"`` to migrate a single specimen by catalog number
    (useful for validating field mapping on the all-fields test record).

Parameters
----------
    oracle_env: str
        Oracle environment prefix (``PROD`` or ``TEST``).
    dry_run: bool
        When True, log actions without writing anything to Specify.
    limit: int | None
        Maximum number of objects to process.  ``None`` processes all.
    only_catalog: str | None
        Migrate one specimen by MUSIT catalog number (e.g. ``O-V-398038``).
    only_object_id: int | None
        Migrate one specimen by Oracle ``OBJECT_ID``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger, task

from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.musit_dataset_loader import DatasetLoadStats, MusitDatasetConfig, load_musit_dataset
from flows.lib.oracle_connectivity import create_oracle_connection, get_oracle_config_from_env
from flows.purge_specify_dataset import _purge_dataset
from flows.lib.specify_setup import setup_django

REPORT_CATEGORY_OSLO_VASCULAR = "oslo-vascular-specimens"

OSLO_VASCULAR_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="O",
    collectioncode="V",
    specify_collection_code="O-V",
    specify_discipline_name="Karplanter Moser",
    dataset_label="oslo-vascular-v1",
)


def _build_report(
    *,
    ts: str,
    oracle_env: str,
    dry_run: bool,
    purge_before_run: bool,
    limit: int | None,
    only_catalog: str | None,
    only_object_id: int | None,
    stats: DatasetLoadStats,
) -> dict[str, Any]:
    return {
        "report_version": 1,
        "flow": "migrate_oslo_vascular",
        "migration_phase": "2.1",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "purge_before_run": purge_before_run,
        "limit": limit,
        "only_catalog": only_catalog,
        "only_object_id": only_object_id,
        "dataset": {
            "oracle_schema": OSLO_VASCULAR_CONFIG.oracle_schema,
            "institutioncode": OSLO_VASCULAR_CONFIG.institutioncode,
            "collectioncode": OSLO_VASCULAR_CONFIG.collectioncode,
            "specify_collection_code": OSLO_VASCULAR_CONFIG.specify_collection_code,
            "specify_discipline_name": OSLO_VASCULAR_CONFIG.specify_discipline_name,
            "dataset_label": OSLO_VASCULAR_CONFIG.dataset_label,
        },
        "stats": {
            "co_created": stats.co_created,
            "co_skipped": stats.co_skipped,
            "ce_created": stats.ce_created,
            "locality_created": stats.locality_created,
            "locality_reused": stats.locality_reused,
            "geography_created": stats.geography_created,
            "determination_created": stats.determination_created,
            "taxon_matched": stats.taxon_matched,
            "taxon_fallback_matched": stats.taxon_fallback_matched,
            "taxon_unresolved": stats.taxon_unresolved,
            "agent_matched": stats.agent_matched,
            "agent_unresolved": stats.agent_unresolved,
            "attachments_created": stats.attachments_created,
            "attachments_failed": stats.attachments_failed,
            "referencework_created": stats.referencework_created,
            "collectionobject_citation_created": stats.collectionobject_citation_created,
            "literature_archived": stats.literature_archived,
            "errors": stats.errors,
            "elapsed_s": round(stats.elapsed_s, 2),
            "estimate_total_s": round(stats.estimate_total_s, 2) if stats.estimate_total_s else None,
        },
    }


@task(retries=0, retry_delay_seconds=10)
def migrate_oslo_vascular_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract Oracle MUSIT objects and load into Specify (single combined task)."""
    setup_django()
    logger = get_run_logger()
    logger.info(
        "migrate_oslo_vascular_task | oracle_env=%s dry_run=%s limit=%s skip_media=%s"
        " only_catalog=%s only_object_id=%s",
        oracle_env, dry_run, limit, skip_media, only_catalog, only_object_id,
    )

    config_obj = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config_obj)
    try:
        with connection.cursor() as cursor:
            stats = load_musit_dataset(
                OSLO_VASCULAR_CONFIG,
                oracle_cursor=cursor,
                dry_run=dry_run,
                limit=limit,
                only_catalog=only_catalog,
                only_object_id=only_object_id,
                run_ts=run_ts,
                skip_media=skip_media,
            )
    finally:
        connection.close()

    logger.info(
        "migrate_oslo_vascular_task done | co_created=%s co_skipped=%s ce=%s"
        " loc_new=%s det=%s taxon_ok=%s agent_ok=%s att_ok=%s att_fail=%s err=%s elapsed=%.1fs%s",
        stats.co_created,
        stats.co_skipped,
        stats.ce_created,
        stats.locality_created,
        stats.determination_created,
        stats.taxon_matched,
        stats.agent_matched,
        stats.attachments_created,
        stats.attachments_failed,
        len(stats.errors),
        stats.elapsed_s,
        f" | estimated_full={round(stats.estimate_total_s, 0)}s"
        if stats.estimate_total_s else "",
    )
    return stats


@flow(
    name="Migrate Oslo Vascular Plants",
    description=(
        "Phase 2: Migrate Oslo vascular herbarium (institutioncode=O, collectioncode=V) "
        "from MUSIT_BOTANIKK_FELLES into Specify 7 collection O-V. "
        "Set limit=100 for a timed test run with throughput estimate, or "
        "only_catalog='O-V-398038' for a single-specimen test."
    ),
)
def migrate_oslo_vascular_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate Oslo vascular plants to Specify 7.

    Args:
        oracle_env: Oracle environment prefix (PROD or TEST).
        dry_run:    When True, logs actions but writes nothing.
        limit:      Stop after N objects; None = full migration.
                    Use limit=100 for a fast test run with time estimate.
        skip_media: When True, skip Unimus TIFF download / asset-server upload.
                    Use for a fast CO/CE/Det pass; backfill media later.
        only_catalog: Migrate one specimen by catalog number (e.g. ``O-V-398038``).
        only_object_id: Migrate one specimen by Oracle ``OBJECT_ID``.
    """
    logger = get_run_logger()
    logger.info(
        "migrate_oslo_vascular_flow | oracle_env=%s dry_run=%s purge_before_run=%s "
        "limit=%s skip_media=%s only_catalog=%s only_object_id=%s",
        oracle_env, dry_run, purge_before_run, limit, skip_media,
        only_catalog, only_object_id,
    )

    setup_django()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    purge_result: dict[str, Any] | None = None
    if purge_before_run and not dry_run:
        logger.warning(
            "purge_before_run=true: deleting previous O-V dataset records before migration "
            "(CollectionObject/CollectingEvent/Locality + objectmap/placemap rows)."
        )
        purge_result = _purge_dataset(
            collection_code=OSLO_VASCULAR_CONFIG.specify_collection_code,
            dry_run=False,
            clear_objectmap_rows=True,
            clear_placemap_rows=True,
            logger=logger,
        )
        logger.info("purge_before_run result: %s", purge_result)

    stats = migrate_oslo_vascular_task(
        oracle_env,
        dry_run,
        limit,
        ts,
        skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )

    report = _build_report(
        ts=ts,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
        stats=stats,
    )
    if purge_result is not None:
        report["purge_before_run_result"] = purge_result
    s3_key = migration_report_s3_key(REPORT_CATEGORY_OSLO_VASCULAR, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info("Uploaded report: %s", uri)

    return {
        "co_created": stats.co_created,
        "co_skipped": stats.co_skipped,
        "ce_created": stats.ce_created,
        "locality_created": stats.locality_created,
        "locality_reused": stats.locality_reused,
        "geography_created": stats.geography_created,
        "determination_created": stats.determination_created,
        "taxon_matched": stats.taxon_matched,
        "taxon_fallback_matched": stats.taxon_fallback_matched,
        "taxon_unresolved": stats.taxon_unresolved,
        "agent_matched": stats.agent_matched,
        "agent_unresolved": stats.agent_unresolved,
        "attachments_created": stats.attachments_created,
        "attachments_failed": stats.attachments_failed,
        "errors": stats.errors,
        "elapsed_s": round(stats.elapsed_s, 2),
        "estimate_total_s": round(stats.estimate_total_s, 2) if stats.estimate_total_s else None,
        "uploaded": uploaded,
        "report_uploaded": bool(uploaded),
        "purge_before_run": purge_before_run,
        "purge_before_run_result": purge_result,
    }
