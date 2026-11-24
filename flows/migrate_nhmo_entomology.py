"""Prefect flow: migrate NHMO entomology (Oslo) into Specify collection NHMO-ENT.

Source is ``MUSIT_ZOOLOGI_ENTOMOLOGI``. That schema has no
``V_OBJECT_ATTRIBUTES`` institution/collection codes. Catalog objects are the
type-29 rows whose ``PARENT_OBJECT_ID`` is the NHMO/ENT node (object 9).

Target is Specify collection ``NHMO-ENT`` in discipline ``Insekter``.
Agents and taxa are matched, not created. Geography and localities are created
on the fly, same as the vascular loader.
"""

from __future__ import annotations

from typing import Any

from prefect import flow, task

from flows.lib.musit_dataset_loader import DatasetLoadStats, MusitDatasetConfig
from flows.migrate_oslo_vascular import _load_vascular_dataset, _run_vascular_flow

REPORT_CATEGORY_NHMO_ENTOMOLOGY = "nhmo-entomology-specimens"

NHMO_ENTOMOLOGY_CONFIG = MusitDatasetConfig(
    oracle_schema="MUSIT_ZOOLOGI_ENTOMOLOGI",
    institutioncode="NHMO",
    collectioncode="ENT",
    specify_collection_code="NHMO-ENT",
    specify_discipline_name="Insekter",
    dataset_label="nhmo-entomology-v1",
    parent_object_id=9,
)


@task(retries=0, retry_delay_seconds=10)
def migrate_nhmo_entomology_task(
    oracle_env: str,
    dry_run: bool,
    limit: int | None,
    run_ts: str,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> DatasetLoadStats:
    """Extract NHMO/ENT catalog objects and load them into Specify collection NHMO-ENT."""
    return _load_vascular_dataset(
        NHMO_ENTOMOLOGY_CONFIG,
        oracle_env=oracle_env,
        dry_run=dry_run,
        limit=limit,
        run_ts=run_ts,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )


@flow(
    name="Migrate NHMO Entomology",
    description=(
        "Phase 2: Migrate NHMO entomology (MUSIT_ZOOLOGI_ENTOMOLOGI, parent object 9) "
        "into Specify collection NHMO-ENT. "
        "Set only_catalog='NHMO-ENT-416090' for a single-specimen test."
    ),
)
def migrate_nhmo_entomology_flow(
    oracle_env: str = "PROD",
    dry_run: bool = True,
    purge_before_run: bool = False,
    limit: int | None = None,
    skip_media: bool = True,
    only_catalog: str | None = None,
    only_object_id: int | None = None,
) -> dict[str, Any]:
    """Migrate NHMO entomology into Specify collection NHMO-ENT."""
    return _run_vascular_flow(
        config=NHMO_ENTOMOLOGY_CONFIG,
        flow_name="migrate_nhmo_entomology",
        report_category=REPORT_CATEGORY_NHMO_ENTOMOLOGY,
        task=migrate_nhmo_entomology_task,
        oracle_env=oracle_env,
        dry_run=dry_run,
        purge_before_run=purge_before_run,
        limit=limit,
        skip_media=skip_media,
        only_catalog=only_catalog,
        only_object_id=only_object_id,
    )
