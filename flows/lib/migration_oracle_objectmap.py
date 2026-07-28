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
    owner = source_owner.upper()
    oid = int(object_id)
    sql = f"""
    SELECT specify_co_id
      FROM {TABLE_NAME}
     WHERE source_owner = %s
       AND source_kind  = %s
       AND source_id    = %s
       AND specify_collection_id = %s
     LIMIT 1
    """
    try:
        with connection.cursor() as cur:
            cur.execute(
                sql,
                [owner[:64], _SOURCE_KIND_CO, str(oid)[:64], int(specify_collection_id)],
            )
            row = cur.fetchone()
            if row is not None:
                return True
    except Exception:  # table may not exist yet on first run
        pass

    from specifyweb.specify.models import Collectionobject

    guid = collectionobject_guid(owner, oid)
    co_id = (
        Collectionobject.objects.filter(
            collection_id=int(specify_collection_id),
            guid=guid,
        )
        .values_list("id", flat=True)
        .first()
    )
    if co_id is None:
        return False

    if backfill_objectmap and run_ts:
        upsert_objectmap_row(
            source_owner=owner,
            source_id=str(oid),
            specify_co_id=int(co_id),
            specify_collection_id=int(specify_collection_id),
            run_ts=run_ts,
            dry_run=False,
        )
    return True
