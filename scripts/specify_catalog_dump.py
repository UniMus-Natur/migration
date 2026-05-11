#!/usr/bin/env python3
"""Dump EVERYTHING linked to a Specify7 catalog number (Karplanter dataset) as JSON.

This script connects directly to the Specify MariaDB (typically via
`source scripts/port-forward.sh db`) and starts from matching
`collectionobject` rows in a collection (default: NHM-karplanter).

It then walks related rows through foreign keys (both directions where safe)
and emits one large JSON document.

Binary columns (BLOB/BINARY/…) are not transferred by default: SQL uses OCTET_LENGTH
only so the wire payload stays small.  Use --include-binary-blobs or
SPECIFY_CATALOG_INCLUDE_BLOBS=1 to pull full binary values.

Examples:
  python scripts/specify_catalog_dump.py --catalog O-V-14399
  python scripts/specify_catalog_dump.py --catalog O-V-14399 --collection-code NHM-karplanter
  python scripts/specify_catalog_dump.py --catalog O-V-14399 --output /tmp/specify-O-V-14399.json
"""

from __future__ import annotations

import argparse
import decimal
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
    if isinstance(v, decimal.Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (bytes, bytearray)):
        return f"<BLOB {len(v):,} bytes>"
    return v


def _lower_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k).lower(): _coerce(v) for k, v in row.items()}


_BLOB_DATA_TYPES = frozenset({"blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary"})


def _fetch_table_column_info(con, schema: str) -> dict[str, list[tuple[str, bool]]]:
    """table_name(lower) -> [(column_name, is_binary_blob), ...] in ordinal order."""
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            [schema],
        )
        rows = cur.fetchall()
    out: dict[str, list[tuple[str, bool]]] = defaultdict(list)
    for r in rows:
        t = str(r["TABLE_NAME"]).lower()
        c = str(r["COLUMN_NAME"])
        is_blob = str(r["DATA_TYPE"]).lower() in _BLOB_DATA_TYPES
        out[t].append((c, is_blob))
    return dict(out)


def _sql_ident(name: str) -> str:
    return "`" + str(name).replace("`", "``") + "`"


def _select_list_for_table(
    table_cols: dict[str, list[tuple[str, bool]]],
    table: str,
    alias: str | None = None,
) -> str:
    """Build SELECT list; binary columns become server-side size placeholders only."""
    cols = table_cols.get(table.lower())
    if not cols:
        return f"{_sql_ident(alias)}.*" if alias else "*"
    prefix = f"{_sql_ident(alias)}." if alias else ""
    parts: list[str] = []
    for col_name, is_blob in cols:
        ident = _sql_ident(col_name)
        qcol = prefix + ident
        if is_blob:
            parts.append(
                f"CASE WHEN {qcol} IS NULL THEN NULL "
                f"ELSE CONCAT('<BLOB ', OCTET_LENGTH({qcol}), ' bytes>') END AS {ident}"
            )
        else:
            parts.append(qcol)
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


# Reverse traversals from these nodes are useful and bounded for one specimen graph.
_ALLOW_REVERSE_FROM = {
    "collectionobject",
    "collectingevent",
    "determination",
    "preparation",
    "collectionobjectattachment",
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
    def __init__(
        self,
        con,
        schema: str,
        table_cols: dict[str, list[tuple[str, bool]]],
        *,
        include_binary_blobs: bool = False,
    ) -> None:
        self.con = con
        self.schema = schema
        self._table_cols = table_cols
        self._include_binary_blobs = include_binary_blobs
        self._select_cache: dict[tuple[str, str | None], str] = {}
        self.outgoing, self.incoming, self.pks = _fetch_fk_maps(con, schema)

        self.rows_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.index_by_pk: dict[str, dict[tuple, dict[str, Any]]] = defaultdict(dict)
        self.seen_pk: dict[str, set[tuple]] = defaultdict(set)
        self.warnings: list[str] = []

    def _select_clause(self, table: str, alias: str | None = None) -> str:
        if self._include_binary_blobs:
            return f"{_sql_ident(alias)}.*" if alias else "*"
        key = (table.lower(), alias)
        if key not in self._select_cache:
            self._select_cache[key] = _select_list_for_table(self._table_cols, table, alias)
        return self._select_cache[key]

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

    def _fetch_rows_where(
        self,
        table: str,
        where_col: str,
        where_val: Any,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sel = self._select_clause(table, None)
        sql = f"SELECT {sel} FROM {_sql_ident(table)} WHERE {_sql_ident(where_col)} = %s"
        params: list[Any] = [where_val]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))
        with self.con.cursor() as cur:
            cur.execute(sql, params)
            return [_lower_keys(r) for r in cur.fetchall()]

    def _fetch_row_by_pk(self, table: str, pk_vals: tuple) -> dict[str, Any] | None:
        pk_cols = self.pks.get(table, [])
        if not pk_cols:
            return None
        where = " AND ".join(f"{_sql_ident(c)} = %s" for c in pk_cols)
        sel = self._select_clause(table, None)
        sql = f"SELECT {sel} FROM {_sql_ident(table)} WHERE {where} LIMIT 1"
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
                    # SQL-level cap prevents expensive full scans/materialization.
                    child_rows = self._fetch_rows_where(
                        fk.from_table,
                        fk.from_col,
                        target_val,
                        limit=5001,
                    )
                except Exception as exc:  # noqa: BLE001
                    self.warnings.append(
                        f"reverse edge {fk.from_table}.{fk.from_col} -> {table}.{fk.to_col} failed: {exc}"
                    )
                    continue

                # hard safety cap per edge to avoid accidental full-table explosions
                if len(child_rows) > 5000:
                    self.warnings.append(
                        f"skipping reverse edge {fk.from_table}.{fk.from_col} -> {table}.{fk.to_col}: "
                        "more than 5000 rows (safety cap)"
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
        help="Specify collection selector (matches collection.code or collection.collectionname; default: NHM-karplanter)",
    )
    parser.add_argument("--output", "-o", help="Write JSON to file instead of stdout")
    parser.add_argument("--compact", action="store_true", help="Compact JSON output")
    parser.add_argument(
        "--include-binary-blobs",
        action="store_true",
        help="Transfer full BLOB/BINARY columns (default: OCTET_LENGTH placeholder in SQL only).",
    )
    args = parser.parse_args()

    cfg = _db_config()
    print(
        f"[specify_catalog_dump] driver={_MYSQL_DRIVER} "
        f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['db']} "
        f"collection={args.collection_code!r}",
        file=sys.stderr,
    )

    con = MySQLdb.connect(**cfg)

    include_binary_blobs = bool(
        args.include_binary_blobs
        or os.getenv("SPECIFY_CATALOG_INCLUDE_BLOBS", "").lower() in ("1", "true", "yes")
    )

    try:
        table_cols = _fetch_table_column_info(con, cfg["db"])
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT c.usergroupscopeid, c.collectionid, c.code, c.collectionname
                FROM collection c
                ORDER BY c.usergroupscopeid
                """
            )
            all_collections = [_lower_keys(r) for r in cur.fetchall()]

        if not all_collections:
            sys.exit("No rows found in collection table.")

        needle = args.collection_code.strip().lower()
        exact = [
            c for c in all_collections
            if str(c.get("code") or "").strip().lower() == needle
            or str(c.get("collectionname") or "").strip().lower() == needle
        ]
        prefix = [
            c for c in all_collections
            if str(c.get("code") or "").strip().lower().startswith(needle)
            or str(c.get("collectionname") or "").strip().lower().startswith(needle)
        ]

        if len(exact) == 1:
            collection = exact[0]
        elif len(exact) > 1:
            choices = ", ".join(
                f"{c.get('code') or '<null-code>'}/{c.get('collectionname') or '<null-name>'}"
                for c in exact
            )
            sys.exit(
                f"Collection selector {args.collection_code!r} is ambiguous (exact matches): {choices}"
            )
        elif len(prefix) == 1:
            collection = prefix[0]
        elif len(prefix) > 1:
            choices = ", ".join(
                f"{c.get('code') or '<null-code>'}/{c.get('collectionname') or '<null-name>'}"
                for c in prefix
            )
            sys.exit(
                f"Collection selector {args.collection_code!r} is ambiguous (prefix matches): {choices}"
            )
        else:
            available = ", ".join(
                f"{c.get('code') or '<null-code>'}/{c.get('collectionname') or '<null-name>'}"
                for c in all_collections
            )
            sys.exit(
                f"Collection selector not found: {args.collection_code!r}\n"
                f"Available collections: {available}"
            )

        collection_member_id = collection.get("usergroupscopeid")
        collection_id = collection.get("collectionid")
        if collection_member_id is None and collection_id is None:
            sys.exit(
                "Selected collection has neither usergroupscopeid nor collectionid; "
                "cannot scope collectionobject lookup."
            )

        co_sel = (
            "co.*"
            if include_binary_blobs
            else _select_list_for_table(table_cols, "collectionobject", "co")
        )
        with con.cursor() as cur:
            if collection_member_id is not None and collection_id is not None:
                cur.execute(
                    f"""
                    SELECT {co_sel}
                    FROM collectionobject co
                    WHERE co.catalognumber = %s
                      AND (co.collectionmemberid = %s OR co.collectionid = %s)
                    ORDER BY co.collectionobjectid
                    """,
                    [args.catalog, collection_member_id, collection_id],
                )
            elif collection_member_id is not None:
                cur.execute(
                    f"""
                    SELECT {co_sel}
                    FROM collectionobject co
                    WHERE co.catalognumber = %s
                      AND co.collectionmemberid = %s
                    ORDER BY co.collectionobjectid
                    """,
                    [args.catalog, collection_member_id],
                )
            else:
                cur.execute(
                    f"""
                    SELECT {co_sel}
                    FROM collectionobject co
                    WHERE co.catalognumber = %s
                      AND co.collectionid = %s
                    ORDER BY co.collectionobjectid
                    """,
                    [args.catalog, collection_id],
                )
            co_rows = [_lower_keys(r) for r in cur.fetchall()]

        if not co_rows:
            sys.exit(
                f"No collectionobject found for catalog={args.catalog!r} in collection={args.collection_code!r}"
            )

        collector = GraphCollector(
            con,
            cfg["db"],
            table_cols,
            include_binary_blobs=include_binary_blobs,
        )
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
                "collection_member_id": collection_member_id,
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
