"""Prefect flow: purge all specimen data for a Specify collection.

This is a **destructive maintenance tool** for staging/test environments.
It removes the Specify specimen records created by a dataset migration flow and
optionally clears the bridge-table tracking rows.

What is deleted (in FK-safe order)
-----------------------------------
1. ``collectionobjectattachment`` (+ linked ``attachment`` rows and asset-server files)
2. ``determination``               linked to the collection's objects
3. ``collectionobjectattr``        linked to the collection's objects
4. ``collectionobject``            all rows for the given ``CollectionID``
5. ``collectingevent``             only those that were exclusively used by the
                                   deleted objects (no remaining CO references)
6. ``locality``                    only orphaned rows (no remaining CE references)
7. ``migration_oracle_objectmap``  rows for this collection (when ``clear_objectmap_rows=True``)
8. ``migration_oracle_placemap``   rows for the discipline (when ``clear_placemap_rows=True``)

Attachment / asset-server cleanup
---------------------------------
``Collectionobjectattachment`` rows are removed via the **Django ORM** (not bulk SQL) so
Specify's ``post_delete`` signals run: the join row delete cascades to ``Attachment``,
which calls the asset server's ``filedelete`` endpoint (same as deleting in the UI).

What is NOT touched
--------------------
* ``Agent``     (shared; migrated separately)
* ``Geography`` (shared; pre-existing)
* ``Taxon``     (shared; pre-existing)
* ``SpecifyUser``
* Migration report objects in the ``specify-migration`` S3 bucket

Specimen tables use **direct SQL** (same strategy as ``purge_specify_geography_locality.py``)
for speed.  For large collections, deletes are chunked to avoid single huge transactions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from prefect import flow, get_run_logger

from flows.lib.migration_oracle_objectmap import TABLE_NAME as OBJECTMAP_TABLE
from flows.lib.migration_oracle_placemap import TABLE_NAME as PLACEMAP_TABLE
from flows.lib.migration_report_s3 import migration_report_s3_key
from flows.lib.migration_report_upload import upload_migration_report_json_task
from flows.lib.specify_setup import setup_django

REPORT_CATEGORY_PURGE_DATASET = "purge-specify-dataset"

_CHUNK = 5000  # rows per DELETE chunk
_ATTACHMENT_ITER_CHUNK = 200  # ORM deletes (one asset-server call each)


def _count_collection_object_attachments(cur: Any, collection_id: int) -> tuple[int, int]:
    """Return (join-row count, distinct attachment count) for a collection."""
    coa_count = _count(
        cur,
        """
        SELECT COUNT(*)
          FROM collectionobjectattachment coa
          JOIN collectionobject co ON co.collectionobjectid = coa.CollectionObjectID
         WHERE co.CollectionID = %s
        """,
        [collection_id],
    )
    att_count = _count(
        cur,
        """
        SELECT COUNT(DISTINCT coa.AttachmentID)
          FROM collectionobjectattachment coa
          JOIN collectionobject co ON co.collectionobjectid = coa.CollectionObjectID
         WHERE co.CollectionID = %s
        """,
        [collection_id],
    )
    return coa_count, att_count


def _purge_collection_object_attachments(
    co_ids: list[int],
    *,
    logger: Any,
) -> dict[str, int]:
    """Delete CO attachments through Specify ORM so asset-server files are removed."""
    from specifyweb.backend.attachment_gw import views as attachment_gw_views
    from specifyweb.specify.models import Collectionobjectattachment

    stats = {
        "collectionobjectattachment_deleted": 0,
        "attachment_deleted": 0,
        "asset_delete_failed": 0,
    }
    if not co_ids:
        return stats

    attachment_gw_views.init()
    asset_server_configured = attachment_gw_views.server_urls is not None
    if asset_server_configured:
        logger.info(
            "purge_specify_dataset | deleting attachments via ORM (asset-server filedelete enabled)"
        )
    else:
        logger.warning(
            "purge_specify_dataset | asset server not configured; "
            "falling back to SQL attachment delete (S3 files will remain)"
        )
        return _purge_collection_object_attachments_sql(co_ids, logger=logger)

    for i in range(0, len(co_ids), _CHUNK):
        batch = co_ids[i : i + _CHUNK]
        coa_qs = (
            Collectionobjectattachment.objects.filter(collectionobject_id__in=batch)
            .select_related("attachment")
            .order_by("id")
        )
        for coa in coa_qs.iterator(chunk_size=_ATTACHMENT_ITER_CHUNK):
            coa_id = int(coa.pk)
            attachment_id = int(coa.attachment_id)
            try:
                coa.delete()
                stats["collectionobjectattachment_deleted"] += 1
                stats["attachment_deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                stats["asset_delete_failed"] += 1
                logger.warning(
                    "purge_specify_dataset | attachment delete failed "
                    "collectionobjectattachment_id=%s attachment_id=%s: %s",
                    coa_id,
                    attachment_id,
                    exc,
                )
    return stats


def _purge_collection_object_attachments_sql(
    co_ids: list[int],
    *,
    logger: Any,
) -> dict[str, int]:
    """Fallback when the asset server is unavailable: SQL-only attachment cleanup."""
    from django.db import connection

    stats = {
        "collectionobjectattachment_deleted": 0,
        "attachment_deleted": 0,
        "asset_delete_failed": 0,
    }
    with connection.cursor() as cur:
        for i in range(0, len(co_ids), _CHUNK):
            batch = co_ids[i : i + _CHUNK]
            placeholders = ",".join(["%s"] * len(batch))
            cur.execute(
                f"""
                SELECT DISTINCT coa.AttachmentID
                  FROM collectionobjectattachment coa
                 WHERE coa.CollectionObjectID IN ({placeholders})
                """,
                batch,
            )
            attachment_ids = [int(row[0]) for row in cur.fetchall() if row and row[0] is not None]

            cur.execute(
                f"DELETE FROM collectionobjectattachment WHERE CollectionObjectID IN ({placeholders})",
                batch,
            )
            stats["collectionobjectattachment_deleted"] += int(
                cur.rowcount if cur.rowcount is not None else 0
            )

            if attachment_ids:
                att_placeholders = ",".join(["%s"] * len(attachment_ids))
                cur.execute(
                    f"DELETE FROM attachment WHERE attachmentid IN ({att_placeholders})",
                    attachment_ids,
                )
                stats["attachment_deleted"] += int(
                    cur.rowcount if cur.rowcount is not None else 0
                )
    logger.info(
        "purge_specify_dataset | SQL attachment fallback deleted %s join rows, %s attachments",
        stats["collectionobjectattachment_deleted"],
        stats["attachment_deleted"],
    )
    return stats


def _table_exists(cur: Any, table: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables"
        " WHERE table_schema = DATABASE() AND table_name = %s",
        [table],
    )
    return bool(cur.fetchone()[0])


def _count(cur: Any, sql: str, params: list | None = None) -> int:
    cur.execute(sql, params or [])
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _purge_dataset(
    *,
    collection_code: str,
    dry_run: bool,
    clear_objectmap_rows: bool,
    clear_placemap_rows: bool,
    logger: Any,
) -> dict[str, Any]:
    """Core purge logic.  Returns a stats dict."""
    from django.db import connection

    out: dict[str, Any] = {
        "collection_code": collection_code,
        "dry_run": dry_run,
        "clear_objectmap_rows": clear_objectmap_rows,
        "clear_placemap_rows": clear_placemap_rows,
    }

    # ---- Resolve Collection and Discipline from Specify ORM ----
    setup_django()
    from specifyweb.specify.models import Collection, Discipline

    collection = Collection.objects.filter(code__iexact=collection_code).first()
    if collection is None:
        out["error"] = f"Collection with code={collection_code!r} not found"
        return out
    collection_id = int(collection.id)
    out["collection_id"] = collection_id

    discipline = collection.discipline
    discipline_id = int(discipline.id) if discipline else None
    out["discipline_id"] = discipline_id

    with connection.cursor() as cur:
        # Count before.
        co_before = _count(cur, "SELECT COUNT(*) FROM collectionobject WHERE CollectionID = %s", [collection_id])
        out["co_before"] = co_before

        if co_before == 0 and not clear_objectmap_rows and not clear_placemap_rows:
            out["message"] = "nothing to purge"
            return out

        coa_before, att_before = _count_collection_object_attachments(cur, collection_id)
        out["collectionobjectattachment_before"] = coa_before
        out["attachment_before"] = att_before

        if dry_run:
            # Count what would be removed.
            out["would_delete_co"] = co_before
            out["would_delete_collectionobjectattachment"] = coa_before
            out["would_delete_attachment"] = att_before

            cur.execute(
                "SELECT CollectingEventID FROM collectionobject"
                " WHERE CollectionID = %s AND CollectingEventID IS NOT NULL",
                [collection_id],
            )
            ce_ids = [row[0] for row in cur.fetchall()]
            out["would_delete_ce"] = len(ce_ids)

            orphan_loc_count = 0
            if ce_ids:
                cur.execute(
                    "SELECT COUNT(DISTINCT LocalityID) FROM collectingevent"
                    " WHERE CollectingEventID IN ({})".format(",".join(["%s"] * len(ce_ids))),
                    ce_ids,
                )
                orphan_loc_count = int((cur.fetchone() or [0])[0])
            out["would_delete_localities_approx"] = orphan_loc_count

            out["message"] = (
                f"dry_run: would delete ~{coa_before} CollectionObjectAttachments "
                f"(~{att_before} asset-server files), "
                f"~{co_before} CollectionObjects, "
                f"~{len(ce_ids)} CollectingEvents, "
                f"~{orphan_loc_count} Localities"
            )
            return out

    # ---- Live purge ----
    with connection.cursor() as cur:
        # Step 1: collect CO ids in batches (outside FK_CHECKS=0 for attachment ORM deletes).
        offset = 0
        all_co_ids: list[int] = []
        while True:
            cur.execute(
                "SELECT collectionobjectid FROM collectionobject"
                " WHERE CollectionID = %s LIMIT %s OFFSET %s",
                [collection_id, _CHUNK, offset],
            )
            batch = [row[0] for row in cur.fetchall()]
            if not batch:
                break
            all_co_ids.extend(batch)
            offset += _CHUNK

    out["co_count"] = len(all_co_ids)
    logger.info("purge_specify_dataset | collection=%s co_count=%s", collection_code, len(all_co_ids))

    attachment_stats = _purge_collection_object_attachments(all_co_ids, logger=logger)
    out.update(attachment_stats)

    with connection.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            # Collect CE ids before deleting COs.
            all_ce_ids: list[int] = []
            for i in range(0, len(all_co_ids), _CHUNK):
                batch = all_co_ids[i : i + _CHUNK]
                cur.execute(
                    "SELECT DISTINCT CollectingEventID FROM collectionobject"
                    " WHERE collectionobjectid IN ({}) AND CollectingEventID IS NOT NULL".format(
                        ",".join(["%s"] * len(batch))
                    ),
                    batch,
                )
                all_ce_ids.extend(row[0] for row in cur.fetchall())
            all_ce_ids = list(set(all_ce_ids))

            # Step 2: delete Determination rows.
            det_deleted = 0
            for i in range(0, len(all_co_ids), _CHUNK):
                batch = all_co_ids[i : i + _CHUNK]
                cur.execute(
                    "DELETE FROM determination WHERE CollectionObjectID IN ({})".format(
                        ",".join(["%s"] * len(batch))
                    ),
                    batch,
                )
                det_deleted += cur.rowcount
            out["determination_deleted"] = det_deleted

            # Step 3: delete CollectionObjectAttr.
            coattr_deleted = 0
            for i in range(0, len(all_co_ids), _CHUNK):
                batch = all_co_ids[i : i + _CHUNK]
                cur.execute(
                    "DELETE FROM collectionobjectattr WHERE CollectionObjectID IN ({})".format(
                        ",".join(["%s"] * len(batch))
                    ),
                    batch,
                )
                coattr_deleted += cur.rowcount
            out["collectionobjectattr_deleted"] = coattr_deleted

            # Step 4: delete CollectionObjects.
            co_deleted = 0
            for i in range(0, len(all_co_ids), _CHUNK):
                batch = all_co_ids[i : i + _CHUNK]
                cur.execute(
                    "DELETE FROM collectionobject WHERE collectionobjectid IN ({})".format(
                        ",".join(["%s"] * len(batch))
                    ),
                    batch,
                )
                co_deleted += cur.rowcount
            out["co_deleted"] = co_deleted

            logger.info("purge_specify_dataset | deleted %s COs + %s determinations", co_deleted, det_deleted)

            # Step 5: collect locality ids for the CEs we are about to delete,
            # then null the FK and delete CEs.
            locality_ids_candidate: set[int] = set()
            ce_deleted = 0
            for i in range(0, len(all_ce_ids), _CHUNK):
                batch = all_ce_ids[i : i + _CHUNK]
                placeholders = ",".join(["%s"] * len(batch))

                # Grab locality ids before nulling.
                cur.execute(
                    f"SELECT DISTINCT LocalityID FROM collectingevent"
                    f" WHERE CollectingEventID IN ({placeholders}) AND LocalityID IS NOT NULL",
                    batch,
                )
                locality_ids_candidate.update(int(r[0]) for r in cur.fetchall())

                cur.execute(
                    f"UPDATE collectingevent SET LocalityID = NULL"
                    f" WHERE CollectingEventID IN ({placeholders})",
                    batch,
                )
                cur.execute(
                    f"DELETE FROM collectingevent WHERE CollectingEventID IN ({placeholders})",
                    batch,
                )
                ce_deleted += cur.rowcount
            out["ce_deleted"] = ce_deleted

            # Step 6: delete orphaned Locality rows (those no longer referenced by any CE).
            loc_deleted = 0
            loc_ids = list(locality_ids_candidate)
            for i in range(0, len(loc_ids), _CHUNK):
                batch = loc_ids[i : i + _CHUNK]
                placeholders = ",".join(["%s"] * len(batch))
                # Only delete if no CE still references this locality.
                cur.execute(
                    f"DELETE FROM locality WHERE localityid IN ({placeholders})"
                    f" AND localityid NOT IN ("
                    f"  SELECT DISTINCT LocalityID FROM collectingevent"
                    f"  WHERE LocalityID IN ({placeholders})"
                    f")",
                    batch + batch,
                )
                loc_deleted += cur.rowcount
            out["locality_deleted_orphan_only"] = loc_deleted

            logger.info(
                "purge_specify_dataset | deleted %s CEs, %s Localities", ce_deleted, loc_deleted
            )

            # Step 7: clear objectmap rows.
            if clear_objectmap_rows and _table_exists(cur, OBJECTMAP_TABLE):
                cur.execute(
                    f"DELETE FROM {OBJECTMAP_TABLE} WHERE specify_collection_id = %s",
                    [collection_id],
                )
                out["objectmap_rows_deleted"] = cur.rowcount
                logger.info("purge_specify_dataset | objectmap rows deleted: %s", cur.rowcount)

            # Step 8: when clearing placemap rows, force-delete mapped localities for this discipline.
            #
            # Why: orphan-only delete above can leave stale localities if they are still referenced
            # by CollectingEvents outside the just-deleted CO set. For "clean re-run" use cases
            # (purge_before_run), remove those localities too by first nulling all CE FK references.
            if clear_placemap_rows and discipline_id and _table_exists(cur, PLACEMAP_TABLE):
                cur.execute(
                    f"SELECT DISTINCT specify_locality_id FROM {PLACEMAP_TABLE}"
                    " WHERE specify_discipline_id = %s AND specify_locality_id IS NOT NULL",
                    [discipline_id],
                )
                mapped_loc_ids = [int(r[0]) for r in cur.fetchall() if r and r[0] is not None]
                force_loc_deleted = 0
                force_ce_nulled = 0
                for i in range(0, len(mapped_loc_ids), _CHUNK):
                    batch = mapped_loc_ids[i : i + _CHUNK]
                    placeholders = ",".join(["%s"] * len(batch))
                    cur.execute(
                        f"UPDATE collectingevent SET LocalityID = NULL"
                        f" WHERE LocalityID IN ({placeholders})",
                        batch,
                    )
                    force_ce_nulled += int(cur.rowcount if cur.rowcount is not None else 0)
                    cur.execute(
                        f"DELETE FROM locality WHERE localityid IN ({placeholders})",
                        batch,
                    )
                    force_loc_deleted += int(cur.rowcount if cur.rowcount is not None else 0)
                out["locality_force_deleted_from_placemap"] = force_loc_deleted
                out["collectingevent_locality_nulled_for_force_delete"] = force_ce_nulled

                cur.execute(
                    f"DELETE FROM {PLACEMAP_TABLE} WHERE specify_discipline_id = %s",
                    [discipline_id],
                )
                out["placemap_rows_deleted"] = cur.rowcount
                logger.info(
                    "purge_specify_dataset | force locality delete=%s ce_nulled=%s placemap rows deleted: %s",
                    force_loc_deleted,
                    force_ce_nulled,
                    cur.rowcount,
                )

            # Backward-compatible total locality metric.
            out["locality_deleted"] = int(
                out.get("locality_deleted_orphan_only", 0) + out.get("locality_force_deleted_from_placemap", 0)
            )

        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")

    out["message"] = "purge complete"
    return out


@flow(
    name="Purge Specify dataset",
    description=(
        "Destructive maintenance: delete all CollectionObjects, CollectingEvents, and orphaned "
        "Localities for a given collection code.  Does NOT touch Agent, Geography, or Taxon. "
        "Optionally clears migration_oracle_objectmap and migration_oracle_placemap rows."
    ),
)
def purge_specify_dataset_flow(
    collection_code: str = "O-V",
    dry_run: bool = True,
    clear_objectmap_rows: bool = True,
    clear_placemap_rows: bool = False,
) -> dict[str, Any]:
    """Purge all specimen records for a Specify collection.

    Args:
        collection_code:       Specify ``Collection.code`` to purge (e.g. ``O-V``).
        dry_run:               When True, report counts but delete nothing.
        clear_objectmap_rows:  Also delete ``migration_oracle_objectmap`` rows for this collection.
        clear_placemap_rows:   Also delete ``migration_oracle_placemap`` rows for the discipline.
                               Set True to force full re-migration of localities from scratch.
    """
    logger = get_run_logger()
    logger.info(
        "purge_specify_dataset_flow | collection=%s dry_run=%s clear_objectmap=%s clear_placemap=%s",
        collection_code, dry_run, clear_objectmap_rows, clear_placemap_rows,
    )

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    result = _purge_dataset(
        collection_code=collection_code,
        dry_run=dry_run,
        clear_objectmap_rows=clear_objectmap_rows,
        clear_placemap_rows=clear_placemap_rows,
        logger=logger,
    )

    if "error" in result:
        logger.error("purge_specify_dataset_flow | %s", result["error"])
    else:
        logger.info("purge_specify_dataset_flow | %s", result.get("message", "done"))

    report = {
        "report_version": 1,
        "flow": "purge_specify_dataset",
        "generated_at_utc": ts,
        **result,
    }
    s3_key = migration_report_s3_key(REPORT_CATEGORY_PURGE_DATASET, ts)
    uploaded = upload_migration_report_json_task(report, s3_key)
    for uri in uploaded:
        logger.info("Uploaded report: %s", uri)

    return {**result, "uploaded": uploaded, "report_uploaded": bool(uploaded)}
