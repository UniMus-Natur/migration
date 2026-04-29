#!/usr/bin/env python3
"""Dump EVERYTHING linked to a Specify7 catalog number (Karplanter dataset) as JSON.

This script connects directly to the Specify MariaDB (typically via
`source scripts/port-forward.sh db`) and starts from matching
`collectionobject` rows in a collection (default: NHM-karplanter).

It then walks related rows through foreign keys (both directions where safe)
and emits one large JSON document.

Examples:
  python scripts/specify_catalog_dump.py --catalog O-V-14399
  python scripts/specify_catalog_dump.py --catalog O-V-14399 --collection-code NHM-karplanter
  python scripts/specify_catalog_dump.py --catalog O-V-14399 --output /tmp/specify-O-V-14399.json
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Load .env from project root (same pattern as helper scripts)
# ---------------------------------------------------------------------------


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key not in os.environ:
                os.environ[key] = val.strip()


_SCRIPTS_DIR = Path(__file__).resolve().parent
_load_dotenv(_SCRIPTS_DIR.parent / ".env")


# ---------------------------------------------------------------------------
# MySQL connection
# ---------------------------------------------------------------------------

_MYSQL_DRIVER = ""
try:
    import MySQLdb  # type: ignore[import-not-found]
    from MySQLdb.cursors import DictCursor  # type: ignore[import-not-found]

    _MYSQL_DRIVER = "mysqlclient"
except Exception:  # noqa: BLE001
    try:
        import pymysql as MySQLdb  # type: ignore[no-redef, import-not-found]
        from pymysql.cursors import DictCursor  # type: ignore[import-not-found]

        _MYSQL_DRIVER = "pymysql"
    except Exception:  # noqa: BLE001
        sys.exit(
            "No MySQL driver found. Install one in your venv:\n"
            "  pip install mysqlclient\n"
            "or\n"
            "  pip install pymysql"
        )


def _db_config() -> dict[str, Any]:
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    name = os.getenv("DB_NAME", "specify")
    user = os.getenv("DB_USER")
    # Prefer DB_PASSWORD; fall back to Bitnami secret key name used in env files.
    pw = os.getenv("DB_PASSWORD") or os.getenv("mariadb-password")

    missing = [k for k, v in {"DB_USER": user, "DB_PASSWORD|mariadb-password": pw}.items() if not v]
    if missing:
        sys.exit(
            f"Missing DB env vars: {', '.join(missing)}\n"
            "  Ensure .env has DB_USER and DB_PASSWORD (or mariadb-password)."
        )

    return {
        "host": host,
        "port": port,
        "user": user,
        "passwd": pw,
        "db": name,
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
    }


# ---------------------------------------------------------------------------
# FK metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FK:
    from_table: str
    from_col: str
    to_table: str
    to_col: str
    constraint: str


def _fetch_fk_maps(con, schema: str) -> tuple[dict[str, list[FK]], dict[str, list[FK]], dict[str, list[str]]]:
    """Return outgoing_fks, incoming_fks, and pk columns per table (lowercase)."""
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT
              TABLE_NAME,
              COLUMN_NAME,
              REFERENCED_TABLE_NAME,
              REFERENCED_COLUMN_NAME,
              CONSTRAINT_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA = %s
              AND REFERENCED_TABLE_NAME IS NOT NULL
            """,
            [schema],
        )
        fk_rows = cur.fetchall()

        cur.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND COLUMN_KEY = 'PRI'
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            [schema],
        )
        pk_rows = cur.fetchall()

    outgoing: dict[str, list[FK]] = defaultdict(list)
    incoming: dict[str, list[FK]] = defaultdict(list)

    for r in fk_rows:
        fk = FK(
            from_table=str(r["TABLE_NAME"]).lower(),
            from_col=str(r["COLUMN_NAME"]).lower(),
            to_table=str(r["REFERENCED_TABLE_NAME"]).lower(),
            to_col=str(r["REFERENCED_COLUMN_NAME"]).lower(),
            constraint=str(r["CONSTRAINT_NAME"]),
        )
        outgoing[fk.from_table].append(fk)
        incoming[fk.to_table].append(fk)

    pks: dict[str, list[str]] = defaultdict(list)
    for r in pk_rows:
        pks[str(r["TABLE_NAME"]).lower()].append(str(r["COLUMN_NAME"]).lower())

    return dict(outgoing), dict(incoming), dict(pks)


# ---------------------------------------------------------------------------
# JSON coercion
# ---------------------------------------------------------------------------


def _coerce(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return f"<BLOB {len(v):,} bytes>"
    return v


def _lower_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): _coerce(v) for k, v in row.items()}


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


# Reverse traversals from these nodes are useful and bounded for one specimen graph.
_ALLOW_REVERSE_FROM = {
    "collectionobject",
    "collectingevent",
    "locality",
    "geography",
    "determination",
    "attachment",
    "agent",
    "taxon",
    "collectionobjectattr",
    "collectionobjectattachment",
    "collector",
    "migration_oracle_objectmap",
}

# Never reverse-follow these high-fanout/shared roots.
_BLOCK_REVERSE_INTO = {
    "collection",
    "discipline",
    "division",
    "institution",
    "taxontreedef",
    "taxontreedefitem",
    "geographytreedef",
    "geographytreedefitem",
    "storage",
    "agenttype",
    "picklist",
}


class GraphCollector:
    def __init__(self, con, schema: str) -> None:
        self.con = con
        self.schema = schema
        self.outgoing, self.incoming, self.pks = _fetch_fk_maps(con, schema)

        self.rows_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.index_by_pk: dict[str, dict[tuple, dict[str, Any]]] = defaultdict(dict)
        self.seen_pk: dict[str, set[tuple]] = defaultdict(set)
        self.warnings: list[str] = []

    def _pk_tuple(self, table: str, row: dict[str, Any]) -> tuple | None:
        pk_cols = self.pks.get(table, [])
        if not pk_cols:
            return None
        vals = []
        for col in pk_cols:
            if col not in row:
                return None
            vals.append(row[col])
        return tuple(vals)

    def _fetch_rows_where(self, table: str, where_col: str, where_val: Any) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM `{table}` WHERE `{where_col}` = %s"
        with self.con.cursor() as cur:
            cur.execute(sql, [where_val])
            return [_lower_keys(r) for r in cur.fetchall()]

    def _fetch_row_by_pk(self, table: str, pk_vals: tuple) -> dict[str, Any] | None:
        pk_cols = self.pks.get(table, [])
        if not pk_cols:
            return None
        where = " AND ".join(f"`{c}` = %s" for c in pk_cols)
        sql = f"SELECT * FROM `{table}` WHERE {where} LIMIT 1"
        with self.con.cursor() as cur:
            cur.execute(sql, list(pk_vals))
            row = cur.fetchone()
        return _lower_keys(row) if row else None

    def _add_row(self, table: str, row: dict[str, Any]) -> tuple | None:
        pk = self._pk_tuple(table, row)
        if pk is not None:
            if pk in self.seen_pk[table]:
                return None
            self.seen_pk[table].add(pk)
            self.index_by_pk[table][pk] = row
        self.rows_by_table[table].append(row)
        return pk

    def collect_from_collectionobjects(self, seed_rows: list[dict[str, Any]]) -> None:
        q: deque[tuple[str, tuple]] = deque()

        for row in seed_rows:
            table = "collectionobject"
            pk = self._add_row(table, row)
            if pk is not None:
                q.append((table, pk))

        while q:
            table, pk_vals = q.popleft()
            row = self.index_by_pk[table].get(pk_vals)
            if not row:
                row = self._fetch_row_by_pk(table, pk_vals)
                if not row:
                    continue

            # 1) Outgoing FK edges (child -> parent)
            for fk in self.outgoing.get(table, []):
                fk_val = row.get(fk.from_col)
                if fk_val is None:
                    continue
                for parent_row in self._fetch_rows_where(fk.to_table, fk.to_col, fk_val):
                    ppk = self._add_row(fk.to_table, parent_row)
                    if ppk is not None:
                        q.append((fk.to_table, ppk))

            # 2) Incoming FK edges (parent -> children), with safety limits
            if table not in _ALLOW_REVERSE_FROM:
                continue

            for fk in self.incoming.get(table, []):
                if table in _BLOCK_REVERSE_INTO:
                    continue
                if fk.from_table in _BLOCK_REVERSE_INTO:
                    continue

                target_val = row.get(fk.to_col)
                if target_val is None:
                    continue

                try:
                    child_rows = self._fetch_rows_where(fk.from_table, fk.from_col, target_val)
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(
                        f"reverse edge {fk.from_table}.{fk.from_col} -> {table}.{fk.to_col} failed: {exc}"
                    )
                    continue

                # hard safety cap per edge to avoid accidental full-table explosions
                if len(child_rows) > 5000:
                    self.warnings.append(
                        f"skipping reverse edge {fk.from_table}.{fk.from_col} -> {table}.{fk.to_col}: "
                        f"{len(child_rows)} rows (safety cap)"
                    )
                    continue

                for child in child_rows:
                    cpk = self._add_row(fk.from_table, child)
                    if cpk is not None:
                        q.append((fk.from_table, cpk))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dump all Specify7 rows linked to a catalog number (Karplanter).",
    )
    parser.add_argument("--catalog", "-c", required=True, help="Catalog number in Specify collectionobject.catalognumber")
    parser.add_argument(
        "--collection-code",
        default="NHM-karplanter",
        help="Specify collection code (default: NHM-karplanter)",
    )
    parser.add_argument("--output", "-o", help="Write JSON to file instead of stdout")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output")
    args = parser.parse_args()

    cfg = _db_config()
    print(
        f"[specify_catalog_dump] driver={_MYSQL_DRIVER} "
        f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['db']} "
        f"collection={args.collection_code!r}",
        file=sys.stderr,
    )

    con = MySQLdb.connect(**cfg)

    try:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT c.collectionid, c.code, c.collectionname
                FROM collection c
                WHERE c.code = %s
                LIMIT 1
                """,
                [args.collection_code],
            )
            collection = cur.fetchone()

        if not collection:
            sys.exit(f"Collection code not found: {args.collection_code!r}")

        collection = _lower_keys(collection)
        collection_id = collection["collectionid"]

        with con.cursor() as cur:
            cur.execute(
                """
                SELECT co.*
                FROM collectionobject co
                WHERE co.collectionid = %s
                  AND co.catalognumber = %s
                ORDER BY co.collectionobjectid
                """,
                [collection_id, args.catalog],
            )
            co_rows = [_lower_keys(r) for r in cur.fetchall()]

        if not co_rows:
            sys.exit(
                f"No collectionobject found for catalog={args.catalog!r} in collection={args.collection_code!r}"
            )

        collector = GraphCollector(con, cfg["db"])
        collector.collect_from_collectionobjects(co_rows)

        # Include collection row explicitly in output even though reverse traversal is blocked.
        collector.rows_by_table["collection"].append(collection)

        # Parse migration JSON payload from text3 when present.
        for row in collector.rows_by_table.get("collectionobject", []):
            text3 = row.get("text3")
            if isinstance(text3, str) and text3.strip().startswith("{"):
                try:
                    row["_text3_json"] = json.loads(text3)
                except Exception:
                    pass

        out: dict[str, Any] = {
            "_meta": {
                "catalog_number_input": args.catalog,
                "collection_code": args.collection_code,
                "collection_id": collection_id,
                "db_name": cfg["db"],
                "extracted_at_utc": datetime.datetime.now(datetime.UTC).isoformat(),
                "matched_collectionobject_count": len(co_rows),
                "tables_included": sorted(collector.rows_by_table.keys()),
                "row_counts": {k: len(v) for k, v in sorted(collector.rows_by_table.items())},
                "warnings": collector.warnings,
            },
            "tables": dict(sorted(collector.rows_by_table.items())),
        }

        json_text = json.dumps(out, indent=None if args.compact else 2, ensure_ascii=False)

        if args.output:
            Path(args.output).write_text(json_text, encoding="utf-8")
            print(f"Written {len(json_text):,} bytes to {args.output}", file=sys.stderr)
        else:
            print(json_text)

    finally:
        con.close()


if __name__ == "__main__":
    main()
