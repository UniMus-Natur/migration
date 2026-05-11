#!/usr/bin/env python3
"""
Export the Specify MariaDB schema reachable from `collectionobject` as JSON.

Queries information_schema for columns and FK relationships, then walks the FK
graph up to `--max-hops` levels from the root table so the output stays bounded.

Usage (standalone):
    python scripts/specify_schema_export.py -o specify-schema.json
    python scripts/specify_schema_export.py --pretty --max-hops 4

The output is consumed by the Mapping Studio SPA (served at /migration-harness/explore).
Credentials come from the same env vars / .env file as specify_catalog_dump.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# .env loading (same pattern as sibling scripts)
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent


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


_load_dotenv(_REPO_ROOT / ".env")

# ---------------------------------------------------------------------------
# MySQL driver
# ---------------------------------------------------------------------------

_MYSQL_DRIVER = ""
try:
    import MySQLdb  # type: ignore[import-not-found]
    from MySQLdb.cursors import DictCursor  # type: ignore[import-not-found]

    _MYSQL_DRIVER = "mysqlclient"
except Exception:  # noqa: BLE001
    try:
        import pymysql as MySQLdb  # type: ignore[no-redef]
        from pymysql.cursors import DictCursor  # type: ignore[import-not-found]

        _MYSQL_DRIVER = "pymysql"
    except Exception:  # noqa: BLE001
        sys.exit(
            "No MySQL driver. Install one:\n"
            "  pip install mysqlclient\n  or\n  pip install pymysql"
        )


def _db_config() -> dict[str, Any]:
    host = os.getenv("DB_HOST", "localhost")
    port = int(os.getenv("DB_PORT", "3306"))
    name = os.getenv("DB_NAME", "specify")
    user = os.getenv("DB_USER")
    pw = os.getenv("DB_PASSWORD") or os.getenv("mariadb-password")
    missing = [k for k, v in {"DB_USER": user, "DB_PASSWORD": pw}.items() if not v]
    if missing:
        sys.exit(
            f"Missing DB env vars: {', '.join(missing)}\n"
            "  Ensure .env has DB_USER and DB_PASSWORD."
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
# Schema queries
# ---------------------------------------------------------------------------

def _fetch_columns(con, schema: str) -> dict[str, list[dict[str, Any]]]:
    """table_name(lower) -> [{name, type, nullable, pk, extra}]"""
    with con.cursor() as cur:
        cur.execute(
            """
            SELECT
                TABLE_NAME,
                COLUMN_NAME,
                COLUMN_TYPE,
                IS_NULLABLE,
                COLUMN_KEY,
                EXTRA,
                ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            [schema],
        )
        rows = cur.fetchall()

    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[str(r["TABLE_NAME"]).lower()].append(
            {
                "name": str(r["COLUMN_NAME"]),
                "type": str(r["COLUMN_TYPE"]),
                "nullable": str(r["IS_NULLABLE"]).upper() == "YES",
                "pk": str(r["COLUMN_KEY"]).upper() == "PRI",
                "auto_increment": "auto_increment" in str(r["EXTRA"]).lower(),
            }
        )
    return dict(out)


def _fetch_fks(con, schema: str) -> list[dict[str, str]]:
    """All FK edges in the schema as flat list."""
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
        rows = cur.fetchall()
    return [
        {
            "from_table": str(r["TABLE_NAME"]).lower(),
            "from_col": str(r["COLUMN_NAME"]).lower(),
            "to_table": str(r["REFERENCED_TABLE_NAME"]).lower(),
            "to_col": str(r["REFERENCED_COLUMN_NAME"]).lower(),
            "constraint": str(r["CONSTRAINT_NAME"]),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Reachability walk (BFS from root table through FK graph)
# ---------------------------------------------------------------------------

# Tables that are shared institution-wide roots — following every FK into them
# would pull the entire schema.  We include them if they are directly referenced
# from a reachable table, but we do NOT follow their outgoing FKs further.
_BLOCK_TRAVERSE = frozenset(
    {
        "collection",
        "discipline",
        "division",
        "institution",
        "taxontreedef",
        "taxontreedefitem",
        "geographytreedef",
        "geographytreedefitem",
        "storagetreedef",
        "storagetreedefitem",
        "storage",
        "agent",
        "agenttype",
        "picklist",
        "picklistitem",
        "specifyuser",
        "spprincipal",
        "spappresource",
        "spappresourcedir",
        "spappresourcedata",
        "spviewsetobj",
    }
)


# Tables from which we follow INCOMING FKs (parent -> child) during the crawl.
# This ensures that children like 'determination' and 'preparation' are included
# even though they are not reachable via outgoing FKs from 'collectionobject'.
_ALLOW_REVERSE = frozenset(
    {
        "collectionobject",
        "collectingevent",
        "determination",
    }
)


def _reachable_tables(
    root: str,
    all_fks: list[dict[str, str]],
    *,
    max_hops: int = 5,
) -> set[str]:
    """BFS over FK graph from root, bounded by max_hops and block list."""
    outgoing: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, set[str]] = defaultdict(set)
    for fk in all_fks:
        outgoing[fk["from_table"]].add(fk["to_table"])
        incoming[fk["to_table"]].add(fk["from_table"])

    visited: set[str] = {root}
    q: deque[tuple[str, int]] = deque([(root, 0)])
    while q:
        table, depth = q.popleft()
        if depth >= max_hops:
            continue
        if table in _BLOCK_TRAVERSE:
            continue

        # 1) Outgoing (child -> parent)
        for neighbour in outgoing.get(table, set()):
            if neighbour not in visited:
                visited.add(neighbour)
                q.append((neighbour, depth + 1))

        # 2) Incoming (parent -> child) - only for specific tables to avoid blowing up.
        if table in _ALLOW_REVERSE:
            for neighbour in incoming.get(table, set()):
                if neighbour not in visited:
                    visited.add(neighbour)
                    q.append((neighbour, depth + 1))
    return visited


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_specify_schema(
    db_cfg: dict[str, Any],
    *,
    root_table: str = "collectionobject",
    max_hops: int = 5,
) -> dict[str, Any]:
    """
    Connect to MariaDB and return specify-schema.json dict.
    Safe to cache in-process: schema changes require a deploy anyway.
    """
    con = MySQLdb.connect(**db_cfg)
    try:
        schema_name = db_cfg["db"]
        all_fks = _fetch_fks(con, schema_name)
        reachable = _reachable_tables(root_table, all_fks, max_hops=max_hops)
        all_cols = _fetch_columns(con, schema_name)
    finally:
        con.close()

    # Build per-table FK lists restricted to reachable tables.
    outgoing_fks: dict[str, list[dict[str, str]]] = defaultdict(list)
    incoming_fks: dict[str, list[dict[str, str]]] = defaultdict(list)
    fk_edges: list[dict[str, str]] = []

    for fk in all_fks:
        if fk["from_table"] not in reachable:
            continue
        entry = {
            "from_table": fk["from_table"],
            "from_col": fk["from_col"],
            "to_table": fk["to_table"],
            "to_col": fk["to_col"],
        }
        outgoing_fks[fk["from_table"]].append(
            {"from_col": fk["from_col"], "to_table": fk["to_table"], "to_col": fk["to_col"]}
        )
        if fk["to_table"] in reachable:
            incoming_fks[fk["to_table"]].append(
                {"from_table": fk["from_table"], "from_col": fk["from_col"], "to_col": fk["to_col"]}
            )
        fk_edges.append(entry)

    tables: dict[str, Any] = {}
    for tname in sorted(reachable):
        tables[tname] = {
            "columns": all_cols.get(tname, []),
            "outgoing_fks": outgoing_fks.get(tname, []),
            "incoming_fks": incoming_fks.get(tname, []),
        }

    return {
        "schema": "migration-harness/specify-schema/v1",
        "root_table": root_table,
        "table_count": len(tables),
        "column_count": sum(len(t["columns"]) for t in tables.values()),
        "tables": tables,
        "fk_edges": fk_edges,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Export Specify MariaDB schema reachable from collectionobject.")
    parser.add_argument("-o", "--output", help="Write JSON to file (default: stdout)")
    parser.add_argument("--pretty", action="store_true", help="Indented JSON")
    parser.add_argument("--root", default="collectionobject", help="Root table (default: collectionobject)")
    parser.add_argument(
        "--max-hops",
        type=int,
        default=5,
        help="FK hop depth from root (default: 5)",
    )
    args = parser.parse_args()

    cfg = _db_config()
    print(
        f"[specify_schema_export] {_MYSQL_DRIVER} "
        f"mysql://{cfg['user']}@{cfg['host']}:{cfg['port']}/{cfg['db']} "
        f"root={args.root} max_hops={args.max_hops}",
        file=sys.stderr,
    )

    result = build_specify_schema(cfg, root_table=args.root, max_hops=args.max_hops)
    text = json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(
            f"Written {len(text):,} bytes to {args.output} "
            f"({result['table_count']} tables, {result['column_count']} columns)",
            file=sys.stderr,
        )
    else:
        print(text)


if __name__ == "__main__":
    main()
