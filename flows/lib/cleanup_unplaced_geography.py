"""Re-point localities off bogus ``hpo:place*`` geography leaves onto Earth, then delete them.

These nodes were created by an old specimen-loader fallback that invented a Continent
(under Earth) named after locality free text when a MUSIT ``PLACE`` had no hierarchical
chain. Free text already lives on ``Locality.localityName`` /
``CollectingEvent.verbatimLocality``.

GUID pattern (only these — not real hierarchy nodes):

    urn:oracle:<schema>:hpo:place<PLACE_ID>
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Real hierarchy GUIDs are ``…:hpo:<digits>``; placeholders are ``…:hpo:place<digits>``.
_PLACEHOLDER_GUID_RE = re.compile(
    r"^urn:oracle:[^:]+:hpo:place\d+$",
    re.IGNORECASE,
)

_BATCH = 2000


def is_unplaced_place_geography_guid(guid: str | None) -> bool:
    """Return True when ``guid`` is a legacy unplaced PLACE placeholder."""
    if not guid:
        return False
    return _PLACEHOLDER_GUID_RE.match(str(guid).strip()) is not None


def cleanup_unplaced_place_geography_placeholders(
    *,
    dry_run: bool,
    geography_treedef_id: int | None = None,
    batch_size: int = _BATCH,
) -> dict[str, Any]:
    """Move localities (and placemap) from placeholder geos to Earth; delete placeholders.

    Safe properties observed on staging after O-V migration:
    - placeholders are leaves (no child geography)
    - each has typically one locality
    - CollectingEvent still points at those localities (locality rows are kept)
    """
    from django.db import connection, transaction

    from flows.lib.migration_oracle_placemap import TABLE_NAME as PLACEMAP_TABLE

    out: dict[str, Any] = {
        "dry_run": dry_run,
        "geography_treedef_id": geography_treedef_id,
        "placeholder_geography_count": 0,
        "localities_repointed": 0,
        "placemap_rows_updated": 0,
        "geography_deleted": 0,
        "earth_by_treedef": {},
        "errors": [],
    }

    def _table_exists(cur: Any, table: str) -> bool:
        cur.execute(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            [table],
        )
        row = cur.fetchone()
        return bool(row and int(row[0]) > 0)

    with connection.cursor() as cur:
        if geography_treedef_id is None:
            cur.execute(
                """
                SELECT DISTINCT GeographyTreeDefID
                  FROM geography
                 WHERE GUID REGEXP %s
                 ORDER BY GeographyTreeDefID
                """,
                [r"^urn:oracle:[^:]+:hpo:place[0-9]+$"],
            )
            treedef_ids = [int(r[0]) for r in cur.fetchall()]
        else:
            treedef_ids = [int(geography_treedef_id)]

        if not treedef_ids:
            out["message"] = "no placeholder geography GUIDs found"
            return out

        earth_by_td: dict[int, int] = {}
        for tid in treedef_ids:
            cur.execute(
                """
                SELECT GeographyID FROM geography
                 WHERE GeographyTreeDefID = %s AND ParentID IS NULL
                 ORDER BY GeographyID
                 LIMIT 1
                """,
                [tid],
            )
            row = cur.fetchone()
            if not row:
                out["errors"].append(f"no Earth root for GeographyTreeDefID={tid}")
                continue
            earth_by_td[tid] = int(row[0])
        out["earth_by_treedef"] = dict(earth_by_td)

        if out["errors"]:
            return out

        # Count placeholders (and refuse if any have children — unexpected).
        cur.execute(
            """
            SELECT g.GeographyID, g.GeographyTreeDefID, g.GUID,
                   (SELECT COUNT(*) FROM geography c WHERE c.ParentID = g.GeographyID) AS nchild
              FROM geography g
             WHERE g.GUID REGEXP %s
               AND (%s IS NULL OR g.GeographyTreeDefID = %s)
            """,
            [
                r"^urn:oracle:[^:]+:hpo:place[0-9]+$",
                geography_treedef_id,
                geography_treedef_id,
            ],
        )
        placeholders: list[tuple[int, int]] = []  # (geo_id, treedef_id)
        with_children = 0
        for geo_id, tid, _guid, nchild in cur.fetchall():
            if int(nchild) > 0:
                with_children += 1
                continue
            placeholders.append((int(geo_id), int(tid)))
        out["placeholder_geography_count"] = len(placeholders)
        out["placeholders_skipped_with_children"] = with_children
        if with_children:
            out["errors"].append(
                f"{with_children} placeholder geography nodes have children; left untouched"
            )

        if dry_run:
            # Estimate locality / placemap impact without writing.
            if placeholders:
                # Sample count via join — full IN list can be large; use temp approach.
                cur.execute(
                    """
                    SELECT COUNT(*) FROM locality l
                     JOIN geography g ON g.GeographyID = l.GeographyID
                     WHERE g.GUID REGEXP %s
                       AND (%s IS NULL OR g.GeographyTreeDefID = %s)
                    """,
                    [
                        r"^urn:oracle:[^:]+:hpo:place[0-9]+$",
                        geography_treedef_id,
                        geography_treedef_id,
                    ],
                )
                out["localities_would_repoint"] = int(cur.fetchone()[0])
                if _table_exists(cur, PLACEMAP_TABLE):
                    cur.execute(
                        f"""
                        SELECT COUNT(*) FROM {PLACEMAP_TABLE} pm
                         JOIN geography g ON g.GeographyID = pm.specify_geography_id
                         WHERE g.GUID REGEXP %s
                           AND (%s IS NULL OR g.GeographyTreeDefID = %s)
                        """,
                        [
                            r"^urn:oracle:[^:]+:hpo:place[0-9]+$",
                            geography_treedef_id,
                            geography_treedef_id,
                        ],
                    )
                    out["placemap_would_update"] = int(cur.fetchone()[0])
                else:
                    out["placemap_would_update"] = 0
            out["message"] = (
                "dry_run: would repoint localities + placemap to Earth and delete "
                f"{len(placeholders)} placeholder geography nodes"
            )
            return out

    # Apply in batches under transactions.
    bs = max(100, min(int(batch_size), 10_000))
    placemap_exists = False
    with connection.cursor() as cur:
        placemap_exists = _table_exists(cur, PLACEMAP_TABLE)

    for i in range(0, len(placeholders), bs):
        batch = placeholders[i : i + bs]
        # Group by treedef so we can set the correct Earth id.
        by_td: dict[int, list[int]] = {}
        for geo_id, tid in batch:
            by_td.setdefault(tid, []).append(geo_id)

        with transaction.atomic():
            with connection.cursor() as cur:
                for tid, geo_ids in by_td.items():
                    earth_id = earth_by_td[tid]
                    placeholders_sql = ", ".join(["%s"] * len(geo_ids))
                    params = [earth_id, *geo_ids]
                    cur.execute(
                        f"""
                        UPDATE locality
                           SET GeographyID = %s
                         WHERE GeographyID IN ({placeholders_sql})
                        """,
                        params,
                    )
                    out["localities_repointed"] += int(cur.rowcount)

                    if placemap_exists:
                        cur.execute(
                            f"""
                            UPDATE {PLACEMAP_TABLE}
                               SET specify_geography_id = %s
                             WHERE specify_geography_id IN ({placeholders_sql})
                            """,
                            params,
                        )
                        out["placemap_rows_updated"] += int(cur.rowcount)

                    # Delete leaves (no children by construction).
                    cur.execute(
                        f"DELETE FROM geography WHERE GeographyID IN ({placeholders_sql})",
                        geo_ids,
                    )
                    out["geography_deleted"] += int(cur.rowcount)

        logger.info(
            "cleanup_unplaced_geography | batch %s-%s / %s deleted_so_far=%s",
            i + 1,
            min(i + bs, len(placeholders)),
            len(placeholders),
            out["geography_deleted"],
        )

    out["message"] = "cleanup complete"
    return out
