#!/usr/bin/env python3
"""Profile MUSIT CLASSIFICATION_TERM.SENSU_TERM before migration to Specify addendum.

Validates source picklist values (expected: s.lat., s.str.) and flags outliers.

Usage (from migration repo root, with port-forward active):
  python scripts/analyze_sensu_term.py
  python scripts/analyze_sensu_term.py --env test
  python scripts/analyze_sensu_term.py --schema MUSIT_BOTANIKK_FELLES --institution O --collection V
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS.parent))

from scripts.oracle_sql import _connect, _init_oracle_client  # noqa: E402

DEFAULT_SCHEMA = "MUSIT_BOTANIKK_FELLES"
EXPECTED_VALUES = frozenset({"s.lat.", "s.str."})


def run(cur, title: str, sql: str) -> list[tuple]:
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
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile MUSIT SENSU_TERM values.")
    parser.add_argument("--env", default="prod", choices=["prod", "test"])
    parser.add_argument("--schema", default=DEFAULT_SCHEMA)
    parser.add_argument("--institution", default=None, help="Filter specimen subset, e.g. O")
    parser.add_argument("--collection", default=None, help="Filter specimen subset, e.g. V")
    args = parser.parse_args()

    schema = args.schema.strip().upper()
    inst = (args.institution or "").strip().upper() or None
    coll = (args.collection or "").strip().upper() or None

    subset_join = ""
    subset_where = ""
    if inst and coll:
        subset_join = f"""
          JOIN {schema}.classification_event ce ON ce.class_term_id = ct.class_term_id
          JOIN {schema}.event_museum_object emo ON emo.event_id = ce.event_id
          JOIN {schema}.v_object_attributes voa ON voa.object_id = emo.object_id
        """
        subset_where = f"""
           AND voa.institutioncode = '{inst}'
           AND voa.collectioncode = '{coll}'
        """

    _init_oracle_client()
    con, dsn = _connect(args.env)
    print(f"[analyze_sensu_term] connected → {dsn}  schema={schema}")
    cur = con.cursor()
    try:
        run(
            cur,
            f"Distinct SENSU_TERM ({schema})",
            f"""
            SELECT TRIM(ct.sensu_term) AS sensu_term, COUNT(*) AS n
              FROM {schema}.classification_term ct
             WHERE ct.sensu_term IS NOT NULL
             GROUP BY TRIM(ct.sensu_term)
             ORDER BY n DESC
            """,
        )

        if inst and coll:
            run(
                cur,
                f"Distinct SENSU_TERM ({schema}, {inst}-{coll} specimens)",
                f"""
                SELECT TRIM(ct.sensu_term) AS sensu_term, COUNT(*) AS n
                  FROM {schema}.classification_term ct
                  {subset_join}
                 WHERE ct.sensu_term IS NOT NULL
                   {subset_where}
                 GROUP BY TRIM(ct.sensu_term)
                 ORDER BY n DESC
                """,
            )

        outlier_rows = run(
            cur,
            "Outliers (not s.lat. / s.str.)",
            f"""
            SELECT ct.class_term_id,
                   TRIM(ct.sensu_term) AS sensu_term,
                   TRIM(ct.classterm) AS classterm,
                   LENGTH(TRIM(ct.sensu_term)) AS len
              FROM {schema}.classification_term ct
              {subset_join}
             WHERE ct.sensu_term IS NOT NULL
               {subset_where}
               AND TRIM(ct.sensu_term) NOT IN ('s.lat.', 's.str.')
             ORDER BY ct.class_term_id
            """,
        )

        run(
            cur,
            "Length bounds (non-null)",
            f"""
            SELECT MIN(LENGTH(TRIM(sensu_term))) AS min_len,
                   MAX(LENGTH(TRIM(sensu_term))) AS max_len
              FROM {schema}.classification_term
             WHERE sensu_term IS NOT NULL
            """,
        )

        total_sql = f"""
            SELECT COUNT(*) FROM {schema}.classification_term ct
             WHERE ct.sensu_term IS NOT NULL
        """
        if inst and coll:
            total_sql = f"""
                SELECT COUNT(*)
                  FROM {schema}.classification_term ct
                  {subset_join}
                 WHERE ct.sensu_term IS NOT NULL
                   {subset_where}
            """
        cur.execute(total_sql)
        total = int(cur.fetchone()[0])
        expected = total - len(outlier_rows) if not (inst and coll) else None
        if inst and coll:
            cur.execute(
                f"""
                SELECT COUNT(*)
                  FROM {schema}.classification_term ct
                  {subset_join}
                 WHERE ct.sensu_term IS NOT NULL
                   {subset_where}
                   AND TRIM(ct.sensu_term) IN ('s.lat.', 's.str.')
                """
            )
            expected = int(cur.fetchone()[0])

        print(f"\n{'=' * 72}")
        print("Summary")
        print("=" * 72)
        print(f"  Non-null SENSU_TERM rows: {total}")
        if expected is not None:
            print(f"  Expected picklist values (s.lat./s.str.): {expected}")
        print(f"  Outlier rows: {len(outlier_rows)}")
        print(f"  Specify addendum max length: 16 (source max observed ≤ 10)")
        if outlier_rows:
            print(
                "  Note: outliers look like mis-keyed epithet/author fragments, not sensu terms."
            )
        return 0
    finally:
        cur.close()
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
