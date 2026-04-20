"""Prefect flow: purge Specify ``Locality`` and ``Geography`` data.

Use this only in migration/staging environments where destroying these tables is intended.
The flow is idempotent and supports ``dry_run``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator, Sequence

from prefect import flow, get_run_logger

from flows.lib.migration_oracle_placemap import TABLE_NAME as PLACEMAP_TABLE
from flows.lib.migration_report_s3 import (
    REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY,
    migration_report_s3_key,
)
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_setup import setup_django


def _pk_batches(qs: Any, *, chunk_size: int) -> Iterator[list[int]]:
    """Yield sorted PK batches without loading a full table into memory."""
    last_pk = 0
    size = max(100, int(chunk_size))
    while True:
        part: Sequence[int] = list(
            qs.filter(pk__gt=last_pk).order_by("pk").values_list("pk", flat=True)[:size]
        )
        if not part:
            return
        out = [int(x) for x in part]
        last_pk = out[-1]
        yield out


def _purge_localities_and_geographies(
    *,
    dry_run: bool,
    clear_placemap_rows: bool,
    locality_batch_size: int,
    geography_batch_size: int,
    logger: Any,
) -> dict[str, Any]:
    """Clear Locality + Geography using Specify ORM, plus optional placemap row cleanup."""
    from django.db import close_old_connections, connection, transaction
    from specifyweb.specify.models import Agentgeography, Collectingevent, Geography, Locality

    out: dict[str, Any] = {
        "dry_run": dry_run,
        "clear_placemap_rows": clear_placemap_rows,
        "locality_batch_size": int(locality_batch_size),
        "geography_batch_size": int(geography_batch_size),
        "collectingevents_with_locality_before": int(Collectingevent.objects.exclude(locality_id=None).count()),
        "locality_count_before": int(Locality.objects.count()),
        "locality_with_geography_before": int(Locality.objects.exclude(geography_id=None).count()),
        "agentgeography_with_geography_before": int(Agentgeography.objects.exclude(geography_id=None).count()),
        "geography_count_before": int(Geography.objects.count()),
        "collectingevents_locality_nulled": 0,
        "localities_deleted": 0,
        "agentgeography_deleted": 0,
        "geography_accepted_cleared": 0,
        "geographies_deleted": 0,
        "placemap_rows_deleted": 0,
    }

    if dry_run:
        out["message"] = (
            "dry_run: would null CollectingEvent.locality, delete all Locality rows, "
            "delete AgentGeography rows, clear Geography.acceptedgeography, and delete all Geography rows"
        )
        return out

    # Locality purge in chunks: null CE links and delete locality slice per transaction.
    l_chunk = max(100, int(locality_batch_size))
    l_batches = 0
    for ids in _pk_batches(Locality.objects.all(), chunk_size=l_chunk):
        l_batches += 1
        with transaction.atomic():
            out["collectingevents_locality_nulled"] += int(
                Collectingevent.objects.filter(locality_id__in=ids).update(locality_id=None)
            )
            loc_deleted, _loc_detail = Locality.objects.filter(pk__in=ids).delete()
            out["localities_deleted"] += int(loc_deleted)
        if l_batches % 20 == 0:
            logger.warning(
                "purge_specify_geography_locality locality progress batches=%s deleted=%s ce_nulled=%s",
                l_batches,
                out["localities_deleted"],
                out["collectingevents_locality_nulled"],
            )
            close_old_connections()

    # AgentGeography can protect Geography rows; remove links before geography delete.
    ag_deleted, _ag_detail = Agentgeography.objects.exclude(geography_id=None).delete()
    out["agentgeography_deleted"] = int(ag_deleted)
    out["geography_accepted_cleared"] = int(
        Geography.objects.exclude(acceptedgeography_id=None).update(acceptedgeography_id=None)
    )

    # Geography purge in chunks (small table today, still keep memory bounded).
    g_chunk = max(100, int(geography_batch_size))
    g_batches = 0
    for ids in _pk_batches(Geography.objects.all(), chunk_size=g_chunk):
        g_batches += 1
        with transaction.atomic():
            geo_deleted, _geo_detail = Geography.objects.filter(pk__in=ids).delete()
            out["geographies_deleted"] += int(geo_deleted)
        if g_batches % 20 == 0:
            logger.warning(
                "purge_specify_geography_locality geography progress batches=%s deleted=%s",
                g_batches,
                out["geographies_deleted"],
            )
            close_old_connections()

    if clear_placemap_rows:
        # Not a Django model table; purge by owner-kind mapping rows directly.
        try:
            with connection.cursor() as cur:
                cur.execute(f"DELETE FROM {PLACEMAP_TABLE}")
                out["placemap_rows_deleted"] = int(cur.rowcount if cur.rowcount is not None else 0)
        except Exception as exc:  # noqa: BLE001
            out["placemap_error"] = str(exc)[:500]

    out["locality_count_after"] = int(Locality.objects.count())
    out["geography_count_after"] = int(Geography.objects.count())
    out["message"] = "purge complete"
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
    locality_batch_size: int = 5000,
    geography_batch_size: int = 1000,
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
        "locality_batch_size": locality_batch_size,
        "geography_batch_size": geography_batch_size,
    }
    logger.warning(
        "purge_specify_geography_locality start dry_run=%s clear_placemap_rows=%s locality_batch_size=%s geography_batch_size=%s",
        dry_run,
        clear_placemap_rows,
        locality_batch_size,
        geography_batch_size,
    )
    manifest["result"] = _purge_localities_and_geographies(
        dry_run=dry_run,
        clear_placemap_rows=clear_placemap_rows,
        locality_batch_size=locality_batch_size,
        geography_batch_size=geography_batch_size,
        logger=logger,
    )
    logger.warning("purge_specify_geography_locality result=%s", manifest["result"])

    s3_key = migration_report_s3_key(REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY, ts)
    uploaded = upload_migration_report_json_task(manifest, s3_key)
    manifest["uploaded"] = uploaded
    manifest["report_uploaded"] = bool(uploaded)
    return manifest

