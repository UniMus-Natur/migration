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


def object_ids_already_migrated(
    source_owner: str,
    specify_collection_id: int,
) -> set[int]:
    """Return the set of Oracle OBJECT_IDs already present in the objectmap for this collection.

    Used at flow start to build an in-memory skip-set for idempotent re-runs.
    """
    sql = f"""
    SELECT source_id
      FROM {TABLE_NAME}
     WHERE source_owner = %s
       AND source_kind  = %s
       AND specify_collection_id = %s
    """
    try:
        with connection.cursor() as cur:
            cur.execute(sql, [source_owner[:64], _SOURCE_KIND_CO, specify_collection_id])
            return {int(row[0]) for row in cur.fetchall()}
    except Exception:  # table may not exist yet on first run
        return set()
