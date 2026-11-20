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

# Same MUSIT view and joins as Oslo. Filter is institution TRH / collection V
# (NTNU University Museum, Trondheim). Specify collection TRH-V / NTNU-karplanter
# shares the Karplanter Moser discipline, so taxon and geography trees are reused.
NTNU_VASCULAR_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="TRH",
    collectioncode="V",
    specify_collection_code="TRH-V",
    specify_discipline_name="Karplanter Moser",
    dataset_label="ntnu-vascular-v1",
)

REPORT_CATEGORY_NTNU_VASCULAR = "ntnu-vascular-specimens"

# Bergen vascular is one MUSIT collection (BG / S). Nordic and global are catalog
# number series, not separate source collections. Numbers at or above this bound
# are the global herbarium; lower numbers (and a null number) are Nordic.
BERGEN_GLOBAL_IDENTIFIER_NUM = 2_000_000

BERGEN_NORDIC_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="BG",
    collectioncode="S",
    specify_collection_code="BG-S",
    specify_discipline_name="Karplanter Moser",
    dataset_label="bergen-nordic-v1",
    identifier_num_below=BERGEN_GLOBAL_IDENTIFIER_NUM,
)

BERGEN_GLOBAL_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="BG",
    collectioncode="S",
    specify_collection_code="BG-G",
    specify_discipline_name="Karplanter Moser",
    dataset_label="bergen-global-v1",
    identifier_num_at_least=BERGEN_GLOBAL_IDENTIFIER_NUM,
)

REPORT_CATEGORY_BERGEN_NORDIC = "bergen-nordic-specimens"
REPORT_CATEGORY_BERGEN_GLOBAL = "bergen-global-specimens"

# Tromsø, Agder (herbarium code KMN) and Stavanger are each one MUSIT collection.
# None of them has a 2_000_000 catalog-number series.
TROMSO_VASCULAR_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="TROM",
    collectioncode="V",
    specify_collection_code="TROM-V",
    specify_discipline_name="Karplanter Moser",
    dataset_label="tromso-vascular-v1",
)

AGDER_VASCULAR_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="KMN",
    collectioncode="V",
    specify_collection_code="KMN-V",
    specify_discipline_name="Karplanter Moser",
    dataset_label="agder-vascular-v1",
)

STAVANGER_VASCULAR_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="SVG",
    collectioncode="V",
    specify_collection_code="SVG-V",
    specify_discipline_name="Karplanter Moser",
    dataset_label="stavanger-vascular-v1",
)

# Tromsø garden herbarium (Hageherbariet). Catalog strings are V-H-<n>-0, so
# V_OBJECT_ATTRIBUTES parses institution V / collection H — not TROM/V.
TROMSO_GARDEN_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_BOTANIKK_FELLES",
    institutioncode="V",
    collectioncode="H",
    specify_collection_code="TROM-V-H",
    specify_discipline_name="Karplanter Moser",
    dataset_label="tromso-garden-v1",
)

REPORT_CATEGORY_TROMSO_VASCULAR = "tromso-vascular-specimens"
REPORT_CATEGORY_AGDER_VASCULAR = "agder-vascular-specimens"
REPORT_CATEGORY_STAVANGER_VASCULAR = "stavanger-vascular-specimens"
REPORT_CATEGORY_TROMSO_GARDEN = "tromso-garden-specimens"


def _build_report(
    *,
    config: MusitDatasetConfig,
    flow_name: str,
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
        "flow": flow_name,
        "migration_phase": "2.1",
        "generated_at_utc": ts,
        "oracle_env": oracle_env,
        "dry_run": dry_run,
        "purge_before_run": purge_before_run,
        "limit": limit,
        "only_catalog": only_catalog,
        "only_object_id": only_object_id,
        "dataset": {
            "oracle_schema": config.oracle_schema,
            "institutioncode": config.institutioncode,
            "collectioncode": config.collectioncode,
            "specify_collection_code": config.specify_collection_code,
            "specify_discipline_name": config.specify_discipline_name,
            "dataset_label": config.dataset_label,
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


def _load_vascular_dataset(
    config: MusitDatasetConfig,
    *,
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool,
    only_catalog: str | None,
    only_object_id: int | None,
) -> DatasetLoadStats:
    """Extract one MUSIT vascular slice and load it into its Specify collection."""
    setup_django()
    logger = get_run_logger()
    logger.info(
        "load_vascular_dataset | %s/%s -> %s | oracle_env=%s dry_run=%s limit=%s "
        "skip_media=%s only_catalog=%s only_object_id=%s",
        config.institutioncode,
        config.collectioncode,
        config.specify_collection_code,
        oracle_env,
        dry_run,
        limit,
        skip_media,
        only_catalog,
        only_object_id,
    )

    config_obj = get_oracle_config_from_env(oracle_env)
    connection = create_oracle_connection(config_obj)
    try:
        with connection.cursor() as cursor:
            stats = load_musit_dataset(
                config,
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
        "load_vascular_dataset done | collection=%s co_created=%s co_skipped=%s ce=%s"
        " loc_new=%s det=%s taxon_ok=%s agent_ok=%s att_ok=%s att_fail=%s err=%s elapsed=%.1fs%s",
        config.specify_collection_code,
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
    """Extract Oslo vascular objects and load them into Specify collection O-V."""
    return _load_vascular_dataset(
        OSLO_VASCULAR_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@task(retries=0, retry_delay_seconds=10)
def migrate_bergen_nordic_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract Bergen Nordic vascular objects (catalog number below 2000000)."""
    return _load_vascular_dataset(
        BERGEN_NORDIC_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@task(retries=0, retry_delay_seconds=10)
def migrate_bergen_global_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract Bergen global vascular objects (catalog number at or above 2000000)."""
    return _load_vascular_dataset(
        BERGEN_GLOBAL_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@task(retries=0, retry_delay_seconds=10)
def migrate_tromso_vascular_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract Tromsø vascular objects (institution TROM, collection V)."""
    return _load_vascular_dataset(
        TROMSO_VASCULAR_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@task(retries=0, retry_delay_seconds=10)
def migrate_agder_vascular_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract Agder vascular objects (institution KMN, collection V)."""
    return _load_vascular_dataset(
        AGDER_VASCULAR_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@task(retries=0, retry_delay_seconds=10)
def migrate_stavanger_vascular_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract Stavanger vascular objects (institution SVG, collection V)."""
    return _load_vascular_dataset(
        STAVANGER_VASCULAR_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@task(retries=0, retry_delay_seconds=10)
def migrate_tromso_garden_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract Tromsø garden herbarium objects (institution V, collection H)."""
    return _load_vascular_dataset(
        TROMSO_GARDEN_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@task(retries=0, retry_delay_seconds=10)
def migrate_ntnu_vascular_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = False,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract NTNU vascular objects and load them into Specify collection TRH-V."""
    return _load_vascular_dataset(
        NTNU_VASCULAR_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


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
    return _run_vascular_flow(
        config=OSLO_VASCULAR_CONFIG,
        flow_name="migrate_oslo_vascular",
        report_category=REPORT_CATEGORY_OSLO_VASCULAR,
        task=migrate_oslo_vascular_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate Bergen Nordic Vascular Plants",
    description=(
        "Phase 2: Migrate Bergen Nordic vascular herbarium (BG/S catalog numbers "
        "below 2000000) from MUSIT_BOTANIKK_FELLES into Specify collection BG-S. "
        "Same loader as Oslo vascular."
    ),
)
def migrate_bergen_nordic_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate Bergen Nordic vascular plants (BG-S numbers below 2000000) to BG-S."""
    return _run_vascular_flow(
        config=BERGEN_NORDIC_CONFIG,
        flow_name="migrate_bergen_nordic",
        report_category=REPORT_CATEGORY_BERGEN_NORDIC,
        task=migrate_bergen_nordic_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate Bergen Global Vascular Plants",
    description=(
        "Phase 2: Migrate Bergen global vascular herbarium (BG/S catalog numbers "
        "at or above 2000000) from MUSIT_BOTANIKK_FELLES into Specify collection BG-G. "
        "Same loader as Oslo vascular."
    ),
)
def migrate_bergen_global_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate Bergen global vascular plants (BG-S numbers from 2000000) to BG-G."""
    return _run_vascular_flow(
        config=BERGEN_GLOBAL_CONFIG,
        flow_name="migrate_bergen_global",
        report_category=REPORT_CATEGORY_BERGEN_GLOBAL,
        task=migrate_bergen_global_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate Tromso Vascular Plants",
    description=(
        "Phase 2: Migrate Tromsø vascular herbarium (institutioncode=TROM, collectioncode=V) "
        "from MUSIT_BOTANIKK_FELLES into Specify collection TROM-V. Same loader as Oslo vascular."
    ),
)
def migrate_tromso_vascular_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate Tromsø vascular plants to Specify collection TROM-V."""
    return _run_vascular_flow(
        config=TROMSO_VASCULAR_CONFIG,
        flow_name="migrate_tromso_vascular",
        report_category=REPORT_CATEGORY_TROMSO_VASCULAR,
        task=migrate_tromso_vascular_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate Agder Vascular Plants",
    description=(
        "Phase 2: Migrate Agder vascular herbarium (institutioncode=KMN, collectioncode=V) "
        "from MUSIT_BOTANIKK_FELLES into Specify collection KMN-V. Same loader as Oslo vascular."
    ),
)
def migrate_agder_vascular_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate Agder (KMN) vascular plants to Specify collection KMN-V."""
    return _run_vascular_flow(
        config=AGDER_VASCULAR_CONFIG,
        flow_name="migrate_agder_vascular",
        report_category=REPORT_CATEGORY_AGDER_VASCULAR,
        task=migrate_agder_vascular_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate Stavanger Vascular Plants",
    description=(
        "Phase 2: Migrate Stavanger vascular herbarium (institutioncode=SVG, collectioncode=V) "
        "from MUSIT_BOTANIKK_FELLES into Specify collection SVG-V. Same loader as Oslo vascular."
    ),
)
def migrate_stavanger_vascular_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate Stavanger vascular plants to Specify collection SVG-V."""
    return _run_vascular_flow(
        config=STAVANGER_VASCULAR_CONFIG,
        flow_name="migrate_stavanger_vascular",
        report_category=REPORT_CATEGORY_STAVANGER_VASCULAR,
        task=migrate_stavanger_vascular_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate Tromso Garden Herbarium",
    description=(
        "Phase 2: Migrate Tromsø garden herbarium (Hageherbariet; institutioncode=V, "
        "collectioncode=H, catalog numbers V-H-…-0) from MUSIT_BOTANIKK_FELLES into "
        "Specify collection TROM-V-H (TROM-Hageherbariet). Same loader as Oslo vascular."
    ),
)
def migrate_tromso_garden_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate Tromsø garden herbarium to Specify collection TROM-V-H."""
    return _run_vascular_flow(
        config=TROMSO_GARDEN_CONFIG,
        flow_name="migrate_tromso_garden",
        report_category=REPORT_CATEGORY_TROMSO_GARDEN,
        task=migrate_tromso_garden_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate NTNU Vascular Plants",
    description=(
        "Phase 2: Migrate NTNU vascular herbarium (institutioncode=TRH, collectioncode=V) "
        "from MUSIT_BOTANIKK_FELLES into Specify 7 collection TRH-V (NTNU-karplanter). "
        "Same loader as Oslo vascular. Set limit=100 for a timed test run."
    ),
)
def migrate_ntnu_vascular_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate NTNU (Trondheim) vascular plants to Specify collection TRH-V."""
    return _run_vascular_flow(
        config=NTNU_VASCULAR_CONFIG,
        flow_name="migrate_ntnu_vascular",
        report_category=REPORT_CATEGORY_NTNU_VASCULAR,
        task=migrate_ntnu_vascular_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


def _run_vascular_flow(
    *,
    config: MusitDatasetConfig,
    flow_name: str,
    report_category: str,
    task: Any,
    oracle_env: str,
    dry_run: bool,
    purge_before_run: bool,
    limit: int | None,
    skip_media: bool,
    only_catalog: str | None,
    only_object_id: int | None,
) -> dict[str, Any]:
    logger = get_run_logger()
    logger.info(
        "%s | %s/%s -> %s | oracle_env=%s dry_run=%s purge_before_run=%s "
        "limit=%s skip_media=%s only_catalog=%s only_object_id=%s",
        flow_name,
        config.institutioncode,
        config.collectioncode,
        config.specify_collection_code,
        oracle_env,
        dry_run,
        purge_before_run,
        limit,
        skip_media,
        only_catalog,
        only_object_id,
    )

    setup_django()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    purge_result: dict[str, Any] | None = None
    if purge_before_run and not dry_run:
        logger.warning(
            "purge_before_run=true: deleting previous %s records before migration "
            "(CollectionObject/CollectingEvent/Locality + objectmap/placemap rows).",
            config.specify_collection_code,
        )
        purge_result = _purge_dataset(
            collection_code=config.specify_collection_code,
            dry_run=False,
            clear_objectmap_rows=True,
            clear_placemap_rows=True,
            logger=logger,
        )
        logger.info("purge_before_run result: %s", purge_result)

    stats = task(
        oracle_env,
        dry_run,
        limit,
        ts,
        skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )

    report = _build_report(
        config=config,
        flow_name=flow_name,
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
    s3_key = migration_report_s3_key(report_category, ts)
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
