"""Durable Oracle place → Specify ``Locality`` / ``Geography`` bridge table in Specify MariaDB.

Created on first non-dry-run load. Used by specimen migration to resolve ``PLACE_ID`` after this
flow has run.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import connection

logger = logging.getLogger(__name__)

TABLE_NAME = "migration_oracle_placemap"


def ensure_placemap_table(*, dry_run: bool) -> dict[str, Any]:
    """Create ``migration_oracle_placemap`` if missing (idempotent)."""
    out: dict[str, Any] = {"table": TABLE_NAME, "created": False, "dry_run": dry_run}
    if dry_run:
        out["message"] = "dry_run: would CREATE TABLE IF NOT EXISTS"
        return out
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
      id INT AUTO_INCREMENT PRIMARY KEY,
      source_owner VARCHAR(64) NOT NULL,
      source_kind VARCHAR(32) NOT NULL,
      source_id VARCHAR(64) NOT NULL,
      specify_geography_id INT NULL,
      specify_locality_id INT NULL,
      specify_discipline_id INT NOT NULL,
      run_ts VARCHAR(32) NOT NULL,
      UNIQUE KEY uq_migration_placemap_source (source_owner, source_kind, source_id, specify_discipline_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    with connection.cursor() as cur:
        cur.execute(ddl)
    out["created"] = True
    logger.info("Ensured table %s exists", TABLE_NAME)
    return out


def upsert_placemap_row(
    *,
    source_owner: str,
    source_kind: str,
    source_id: str,
    specify_geography_id: int | None,
    specify_locality_id: int | None,
    specify_discipline_id: int,
    run_ts: str,
    dry_run: bool,
) -> None:
    """Insert or replace one mapping row (per discipline for locality)."""
    if dry_run:
        return
    sql = f"""
    INSERT INTO {TABLE_NAME}
      (source_owner, source_kind, source_id, specify_geography_id, specify_locality_id,
       specify_discipline_id, run_ts)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      specify_geography_id = VALUES(specify_geography_id),
      specify_locality_id = VALUES(specify_locality_id),
      run_ts = VALUES(run_ts)
    """
    with connection.cursor() as cur:
        cur.execute(
            sql,
            [
                source_owner[:64],
                source_kind[:32],
                source_id[:64],
                specify_geography_id,
                specify_locality_id,
                specify_discipline_id,
                run_ts[:32],
            ],
        )
