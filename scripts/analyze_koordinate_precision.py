#!/usr/bin/env python3
"""Analyze MUSIT KOORDINATE_PLACE PRECISION and ACCURACY in Oracle PROD."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS.parent))

from scripts.oracle_sql import _init_oracle_client, _connect  # noqa: E402

SCHEMA = "MUSIT_BOTANIKK_FELLES"

OV_PLACE_SUBQUERY = f"""
    SELECT DISTINCT por.place_id
      FROM {SCHEMA}.v_object_attributes voa
      JOIN {SCHEMA}.event_museum_object emo
        ON emo.object_id = voa.object_id
      JOIN {SCHEMA}.collecting_event ce
        ON ce.event_id = emo.event_id
      JOIN {SCHEMA}.place_event_role por
        ON por.event_id = ce.event_id
     WHERE voa.institutioncode = 'O'
       AND voa.collectioncode = 'V'
       AND por.place_id IS NOT NULL
"""

KP_JOIN = f"""
    FROM {SCHEMA}.koordinate_place kp
    JOIN {SCHEMA}.koordinate_place_place kpp
      ON kpp.koordinate_place_id = kp.koordinate_place_id
    LEFT JOIN {SCHEMA}.types t
      ON t.type_id = kp.coordinate_type
"""


def run(cur, title: str, sql: str) -> None:
    print(f"\n{'=' * 72}")
    print(title)
    print("=" * 72)
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows) if rows else [0]) for i, c in enumerate(cols)]
    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*cols))
    print("-" * (sum(widths) + 2 * (len(cols) - 1)))
    for row in rows:
        print(fmt.format(*[str(v) if v is not None else "" for v in row]))
    if not rows:
        print("(no rows)")
    print(f"({len(rows)} rows)")


def main() -> None:
    _init_oracle_client()
    con, _dsn = _connect("PROD")
    cur = con.cursor()
    try:
        run(
            cur,
            "Oracle column comments (KOORDINATE_PLACE.PRECISION / ACCURACY)",
            f"""
            SELECT column_name, comments
              FROM all_col_comments
             WHERE owner = '{SCHEMA}'
               AND table_name = 'KOORDINATE_PLACE'
               AND column_name IN ('PRECISION', 'ACCURACY')
             ORDER BY column_name
            """,
        )

        for label, place_filter in [
            ("All botany KOORDINATE_PLACE rows", ""),
            ("O-V collecting-event places only", f"JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id"),
        ]:
            run(
                cur,
                f"Fill rates — {label}",
                f"""
                SELECT
                    COUNT(*) AS kp_rows,
                    COUNT(kp."PRECISION") AS precision_populated,
                    ROUND(100 * COUNT(kp."PRECISION") / NULLIF(COUNT(*), 0), 1) AS precision_pct,
                    COUNT(kp.accuracy) AS accuracy_populated,
                    ROUND(100 * COUNT(kp.accuracy) / NULLIF(COUNT(*), 0), 1) AS accuracy_pct,
                    COUNT(CASE WHEN kp."PRECISION" IS NOT NULL AND kp.accuracy IS NOT NULL THEN 1 END) AS both_populated,
                    COUNT(CASE WHEN kp."PRECISION" IS NOT NULL AND kp.accuracy IS NULL THEN 1 END) AS precision_only,
                    COUNT(CASE WHEN kp."PRECISION" IS NULL AND kp.accuracy IS NOT NULL THEN 1 END) AS accuracy_only
                  {KP_JOIN}
                  {place_filter}
                """,
            )

        for label, place_filter in [
            ("All botany", ""),
            ("O-V only", f"JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id"),
        ]:
            run(
                cur,
                f"PRECISION value distribution — {label}",
                f"""
                SELECT kp."PRECISION" AS precision_val,
                       COUNT(*) AS cnt,
                       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
                  {KP_JOIN}
                  {place_filter}
                 WHERE kp."PRECISION" IS NOT NULL
                 GROUP BY kp."PRECISION"
                 ORDER BY cnt DESC
                 FETCH FIRST 30 ROWS ONLY
                """,
            )

            run(
                cur,
                f"ACCURACY value distribution — {label}",
                f"""
                SELECT kp.accuracy AS accuracy_val,
                       COUNT(*) AS cnt,
                       ROUND(100 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
                  {KP_JOIN}
                  {place_filter}
                 WHERE kp.accuracy IS NOT NULL
                 GROUP BY kp.accuracy
                 ORDER BY cnt DESC
                 FETCH FIRST 30 ROWS ONLY
                """,
            )

        run(
            cur,
            "PRECISION vs ACCURACY pairs (O-V, top 25)",
            f"""
            SELECT kp."PRECISION" AS precision_val,
                   kp.accuracy AS accuracy_val,
                   COUNT(*) AS cnt
              {KP_JOIN}
              JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id
             WHERE kp."PRECISION" IS NOT NULL OR kp.accuracy IS NOT NULL
             GROUP BY kp."PRECISION", kp.accuracy
             ORDER BY cnt DESC
             FETCH FIRST 25 ROWS ONLY
            """,
        )

        run(
            cur,
            "PRECISION by coordinate type (O-V)",
            f"""
            SELECT NVL(t.typeterm, '(null)') AS coord_type,
                   COUNT(*) AS total,
                   COUNT(kp."PRECISION") AS with_precision,
                   ROUND(AVG(kp."PRECISION"), 2) AS avg_precision,
                   MIN(kp."PRECISION") AS min_prec,
                   MAX(kp."PRECISION") AS max_prec
              {KP_JOIN}
              JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id
             GROUP BY t.typeterm
             ORDER BY total DESC
             FETCH FIRST 20 ROWS ONLY
            """,
        )

        run(
            cur,
            "Decimal places in lat/long vs PRECISION (O-V, where both present)",
            f"""
            SELECT kp."PRECISION" AS precision_val,
                   COUNT(*) AS cnt,
                   ROUND(AVG(
                     GREATEST(
                       NVL(LENGTH(REGEXP_SUBSTR(TO_CHAR(kp.latitude_l), '\\.[0-9]+')) - 1, 0),
                       NVL(LENGTH(REGEXP_SUBSTR(TO_CHAR(kp.longitude_l), '\\.[0-9]+')) - 1, 0)
                     )
                   ), 2) AS avg_decimal_places_latlong,
                   MIN(kp.latitude_l) AS min_lat,
                   MAX(kp.latitude_l) AS max_lat
              {KP_JOIN}
              JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id
             WHERE kp."PRECISION" IS NOT NULL
               AND kp.latitude_l IS NOT NULL
               AND kp.longitude_l IS NOT NULL
             GROUP BY kp."PRECISION"
             ORDER BY precision_val
            """,
        )

        run(
            cur,
            "Sample rows per PRECISION value (O-V, 2 each)",
            f"""
            SELECT precision_val, koordinate_place_id, coordinate_string, latitude_l, longitude_l,
                   accuracy, mgrs_l, zone, datum, coord_type
              FROM (
                SELECT kp."PRECISION" AS precision_val,
                       kp.koordinate_place_id,
                       SUBSTR(kp.coordinate_string, 1, 40) AS coordinate_string,
                       kp.latitude_l,
                       kp.longitude_l,
                       kp.accuracy,
                       kp.mgrs_l,
                       kp.zone,
                       kp.datum,
                       t.typeterm AS coord_type,
                       ROW_NUMBER() OVER (
                         PARTITION BY kp."PRECISION"
                         ORDER BY kp.koordinate_place_id
                       ) AS rn
                  {KP_JOIN}
                  JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id
                 WHERE kp."PRECISION" IS NOT NULL
              )
             WHERE rn <= 2
             ORDER BY precision_val, koordinate_place_id
            """,
        )

        run(
            cur,
            "MGRS coordinate string length vs PRECISION (O-V, MGRS rows)",
            f"""
            SELECT kp."PRECISION" AS precision_val,
                   COUNT(*) AS cnt,
                   ROUND(AVG(LENGTH(kp.coordinate_string)), 1) AS avg_coord_str_len,
                   MIN(LENGTH(kp.coordinate_string)) AS min_len,
                   MAX(LENGTH(kp.coordinate_string)) AS max_len
              {KP_JOIN}
              JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id
             WHERE kp."PRECISION" IS NOT NULL
               AND (kp.mgrs_l IS NOT NULL OR UPPER(kp.coordinate_string) LIKE '%MGRS%'
                    OR REGEXP_LIKE(kp.coordinate_string, '^[0-9]{2}[A-Z]'))
             GROUP BY kp."PRECISION"
             ORDER BY precision_val
            """,
        )

        run(
            cur,
            "Large ACCURACY values (O-V, > 1000 or non-integer-looking)",
            f"""
            SELECT kp.koordinate_place_id,
                   kp."PRECISION",
                   kp.accuracy,
                   SUBSTR(kp.coordinate_string, 1, 35) AS coordinate_string,
                   kp.latitude_l,
                   kp.longitude_l,
                   t.typeterm AS coord_type
              {KP_JOIN}
              JOIN ({OV_PLACE_SUBQUERY}) ov ON ov.place_id = kpp.place_id
             WHERE kp.accuracy IS NOT NULL
               AND (kp.accuracy > 1000 OR kp.accuracy < 0
                    OR kp.accuracy != TRUNC(kp.accuracy))
             ORDER BY kp.accuracy DESC
             FETCH FIRST 20 ROWS ONLY
            """,
        )

    finally:
        cur.close()
        con.close()


if __name__ == "__main__":
    main()
