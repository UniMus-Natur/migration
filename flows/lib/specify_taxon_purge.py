"""Remove all Specify ``Taxon`` tree data (all disciplines).

Use only on migration/staging databases. Does **not** touch ``Agent`` or ``SpecifyUser``.
Clears taxon FKs on specimen tables first, then truncates taxon + tree-definition tables and
unlinks ``Discipline.TaxonTreeDefID``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def purge_all_taxon_trees(*, dry_run: bool) -> dict[str, Any]:
    """Delete every ``Taxon`` row and related tree metadata."""
    from django.db import connection

    out: dict[str, Any] = {
        "dry_run": dry_run,
        "taxon_count_before": 0,
        "taxontreedef_count_before": 0,
        "discipline_links_before": 0,
        "tables_truncated": [],
        "discipline_treedef_unlinked": 0,
    }

    def _table_exists(cur: Any, table: str) -> bool:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables"
            " WHERE table_schema = DATABASE() AND table_name = %s",
            [table],
        )
        return bool(cur.fetchone()[0])

    def _count(cur: Any, sql: str) -> int:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] if row and row[0] is not None else 0)

    with connection.cursor() as cur:
        if _table_exists(cur, "taxon"):
            out["taxon_count_before"] = _count(cur, "SELECT COUNT(*) FROM taxon")
        if _table_exists(cur, "taxontreedef"):
            out["taxontreedef_count_before"] = _count(cur, "SELECT COUNT(*) FROM taxontreedef")
        if _table_exists(cur, "discipline"):
            out["discipline_links_before"] = _count(
                cur, "SELECT COUNT(*) FROM discipline WHERE TaxonTreeDefID IS NOT NULL"
            )

    if dry_run:
        out["message"] = (
            "dry_run: would null taxon FKs on specimen tables and TRUNCATE taxon tree tables"
        )
        return out

    # Child / junction tables first, then taxon, then rank defs.
    truncate_order = [
        "commonname",
        "taxonattachment",
        "taxoncitation",
        "taxon",
        "taxontreedefitem",
        "taxontreedef",
    ]

    with connection.cursor() as cur:
        # Clear references from tables we keep (specimen side should already be empty).
        if _table_exists(cur, "determination"):
            cur.execute("UPDATE determination SET TaxonID = NULL, PreferredTaxonID = NULL")
            out["determination_taxon_nulled"] = int(cur.rowcount or 0)
        if _table_exists(cur, "collectingeventattribute"):
            cur.execute("UPDATE collectingeventattribute SET HostTaxonID = NULL WHERE HostTaxonID IS NOT NULL")
            out["collectingeventattribute_hosttaxon_nulled"] = int(cur.rowcount or 0)
        if _table_exists(cur, "component"):
            cur.execute("UPDATE component SET TaxonID = NULL WHERE TaxonID IS NOT NULL")
            out["component_taxon_nulled"] = int(cur.rowcount or 0)

        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        try:
            for table in truncate_order:
                if _table_exists(cur, table):
                    cur.execute(f"TRUNCATE TABLE {table}")
                    out["tables_truncated"].append(table)
            if _table_exists(cur, "discipline"):
                cur.execute("UPDATE discipline SET TaxonTreeDefID = NULL WHERE TaxonTreeDefID IS NOT NULL")
                out["discipline_treedef_unlinked"] = int(cur.rowcount or 0)
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS=1")

        out["taxon_count_after"] = _count(cur, "SELECT COUNT(*) FROM taxon") if _table_exists(cur, "taxon") else 0
        out["taxontreedef_count_after"] = (
            _count(cur, "SELECT COUNT(*) FROM taxontreedef") if _table_exists(cur, "taxontreedef") else 0
        )

    out["message"] = "taxon tree purge complete"
    logger.warning(
        "purge_all_taxon_trees done taxon_before=%s truncated=%s",
        out["taxon_count_before"],
        out["tables_truncated"],
    )
    return out
