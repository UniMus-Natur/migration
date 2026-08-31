"""Durable Oracle object → Specify ``CollectionObject`` bridge table in Specify MariaDB.

Created on first non-dry-run load. Mirrors ``migration_oracle_placemap`` but tracks
specimen-level objects rather than geography/locality.  Used by the specimen migration
flows and the dataset purge flow.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import connection

logger = logging.getLogger(__name__)

TABLE_NAME = "migration_oracle_objectmap"

_SOURCE_KIND_CO = "collectionobject"


def collectionobject_guid_prefix(source_owner: str) -> str:
    """Return the GUID prefix used for migrated CollectionObjects from ``source_owner``."""
    return f"urn:oracle:{source_owner.lower()}:object:"


def collectionobject_guid(source_owner: str, object_id: int) -> str:
    """Stable GUID written on migrated CollectionObjects for ``object_id``."""
    return f"{collectionobject_guid_prefix(source_owner)}{int(object_id)}"[:128]


def ensure_objectmap_table(*, dry_run: bool) -> dict[str, Any]:
    """Create ``migration_oracle_objectmap`` if missing (idempotent)."""
    out: dict[str, Any] = {"table": TABLE_NAME, "created": False, "dry_run": dry_run}
    if dry_run:
        out["message"] = "dry_run: would CREATE TABLE IF NOT EXISTS"
        return out
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
      id                    INT AUTO_INCREMENT PRIMARY KEY,
      source_owner          VARCHAR(64)  NOT NULL,
      source_kind           VARCHAR(32)  NOT NULL,
      source_id             VARCHAR(64)  NOT NULL,
      specify_co_id         INT          NOT NULL,
      specify_collection_id INT          NOT NULL,
      run_ts                VARCHAR(32)  NOT NULL,
      UNIQUE KEY uq_objectmap (source_owner, source_kind, source_id, specify_collection_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with connection.cursor() as cur:
        cur.execute(ddl)
    out["created"] = True
    logger.info("Ensured table %s exists", TABLE_NAME)
    return out


def upsert_objectmap_row(
    *,
    source_owner: str,
    source_id: str,
    specify_co_id: int,
    specify_collection_id: int,
    run_ts: str,
    dry_run: bool,
) -> None:
    """Insert or update one object mapping row (idempotent on re-run)."""
    if dry_run:
        return
    sql = f"""
    INSERT INTO {TABLE_NAME}
      (source_owner, source_kind, source_id, specify_co_id, specify_collection_id, run_ts)
    VALUES (%s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      specify_co_id         = VALUES(specify_co_id),
      run_ts                = VALUES(run_ts)
    """
    with connection.cursor() as cur:
        cur.execute(
            sql,
            [
                source_owner[:64],
                _SOURCE_KIND_CO[:32],
                source_id[:64],
                specify_co_id,
                specify_collection_id,
                run_ts[:32],
            ],
        )


def is_object_already_migrated(
    *,
    source_owner: str,
    object_id: int,
    specify_collection_id: int,
    run_ts: str | None = None,
    backfill_objectmap: bool = True,
) -> bool:
    """Return True when this Oracle OBJECT_ID is already present in Specify.

    Checks ``migration_oracle_objectmap`` first (indexed unique key), then falls back to
    the deterministic CollectionObject GUID. When found only via GUID and
    ``backfill_objectmap`` is True, writes the missing objectmap row.
    """
    done = filter_already_migrated_object_ids(
        source_owner=source_owner,
        object_ids=[int(object_id)],
        specify_collection_id=specify_collection_id,
        run_ts=run_ts,
        backfill_objectmap=backfill_objectmap,
    )
    return int(object_id) in done


def filter_already_migrated_object_ids(
    *,
    source_owner: str,
    object_ids: list[int],
    specify_collection_id: int,
    run_ts: str | None = None,
    backfill_objectmap: bool = True,
) -> set[int]:
    """Return the subset of ``object_ids`` already present in Specify.

    One objectmap IN-query plus one GUID IN-query for the remainder — avoids a
    per-object round-trip when paging Oracle IDs.
    """
    if not object_ids:
        return set()

    owner = source_owner.upper()
    coll_id = int(specify_collection_id)
    wanted = {int(oid) for oid in object_ids}
    found: set[int] = set()

    id_list = sorted(wanted)
    # Chunk IN-lists to keep query size bounded.
    chunk = 500
    try:
        with connection.cursor() as cur:
            for i in range(0, len(id_list), chunk):
                part = id_list[i : i + chunk]
                placeholders = ", ".join(["%s"] * len(part))
                sql = f"""
                SELECT source_id
                  FROM {TABLE_NAME}
                 WHERE source_owner = %s
                   AND source_kind  = %s
                   AND specify_collection_id = %s
                   AND source_id IN ({placeholders})
                """
                cur.execute(
                    sql,
                    [owner[:64], _SOURCE_KIND_CO, coll_id, *[str(oid)[:64] for oid in part]],
                )
                for (source_id,) in cur.fetchall():
                    found.add(int(source_id))
    except Exception:  # table may not exist yet on first run
        pass

    missing = sorted(wanted - found)
    if not missing:
        return found

    from specifyweb.specify.models import Collectionobject

    guid_to_oid = {collectionobject_guid(owner, oid): oid for oid in missing}
    for i in range(0, len(missing), chunk):
        part_guids = [collectionobject_guid(owner, oid) for oid in missing[i : i + chunk]]
        rows = Collectionobject.objects.filter(
            collection_id=coll_id,
            guid__in=part_guids,
        ).values_list("id", "guid")
        for co_id, guid in rows:
            oid = guid_to_oid.get(str(guid))
            if oid is None:
                continue
            found.add(oid)
            if backfill_objectmap and run_ts:
                upsert_objectmap_row(
                    source_owner=owner,
                    source_id=str(oid),
                    specify_co_id=int(co_id),
                    specify_collection_id=coll_id,
                    run_ts=run_ts,
                    dry_run=False,
                )
    return found
