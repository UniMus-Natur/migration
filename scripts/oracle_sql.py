#!/usr/bin/env python3
"""Run SQL on Oracle PROD/TEST via port-forwarded localhost.

Requires: source scripts/port-forward.sh first (sets up localhost tunnels).

  Tunnel mapping:
    PROD  localhost:1553  ← dbora-musit-prod03.uio.no:1553
    TEST  localhost:1554  ← dbora-musit-utv03.uio.no:1553

Usage:
  oracle_sql "SELECT sysdate FROM dual"
  echo "SELECT * FROM some_table WHERE ROWNUM <= 10" | oracle_sql
  oracle_sql --env test "SELECT 1 FROM dual"
  oracle_sql --csv "SELECT owner, table_name FROM dba_tables WHERE ROWNUM <= 20"
  oracle_sql --list-schemas

Thick mode (required for PROD native network encryption):
  macOS:  brew install --cask oracle-instantclient
          export ORACLE_CLIENT_LIB_DIR=/usr/local/lib
  Set ORACLE_USE_THICK_MODE=false to force thin (TEST only).
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env from project root if vars not already exported
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
# oracledb import + thick-mode init
# ---------------------------------------------------------------------------

try:
    import oracledb
except ImportError:
    sys.exit("oracledb not found. Activate the project venv first:\n  source .venv/bin/activate")

_CLIENT_INITIALIZED = False


def _init_oracle_client() -> str:
    """Initialise thick mode when requested; returns the mode string used."""
    global _CLIENT_INITIALIZED
    if _CLIENT_INITIALIZED:
        return _current_mode  # type: ignore[name-defined]

    use_thick = os.getenv("ORACLE_USE_THICK_MODE", "true").strip().lower() in {
        "1", "true", "yes", "on",
    }
    if not use_thick:
        _CLIENT_INITIALIZED = True
        _set_mode("thin")
        return "thin"

    lib_dir = os.getenv("ORACLE_CLIENT_LIB_DIR") or None
    try:
        if lib_dir:
            oracledb.init_oracle_client(lib_dir=lib_dir)
        else:
            oracledb.init_oracle_client()
        _set_mode("thick")
        _CLIENT_INITIALIZED = True
        return "thick"
    except Exception as exc:
        print(
            f"Warning: thick-mode init failed — {exc}\n"
            "  PROD requires native network encryption (thick mode).\n"
            "  Install Oracle Instant Client to connect to PROD:\n"
            "    macOS:  brew install --cask oracle-instantclient\n"
            "            export ORACLE_CLIENT_LIB_DIR=/usr/local/lib\n"
            "  To use thin mode (TEST only): export ORACLE_USE_THICK_MODE=false",
            file=sys.stderr,
        )
        _set_mode("thin (fallback)")
        _CLIENT_INITIALIZED = True
        return "thin (fallback)"


_current_mode: str = "unknown"


def _set_mode(m: str) -> None:
    global _current_mode
    _current_mode = m


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

# Local port mapping — matches port-forward.sh tunnel ports
_LOCAL_PORTS = {
    "prod": 1553,
    "test": 1554,
}


def _connect(env: str):
    prefix = env.upper()
    local_port = _LOCAL_PORTS[env.lower()]

    user = os.environ.get(f"ORACLE_{prefix}_USER")
    password = os.environ.get(f"ORACLE_{prefix}_PASSWORD")
    service = os.environ.get(f"ORACLE_{prefix}_SERVICE")

    missing = [
        k for k, v in {
            f"ORACLE_{prefix}_USER": user,
            f"ORACLE_{prefix}_PASSWORD": password,
            f"ORACLE_{prefix}_SERVICE": service,
        }.items() if not v
    ]
    if missing:
        sys.exit(
            f"Missing env vars: {', '.join(missing)}\n"
            "  Run: source scripts/port-forward.sh  (or load your .env)"
        )

    dsn = f"localhost:{local_port}/{service}"
    con = oracledb.connect(user=user, password=password, dsn=dsn)
    return con, dsn


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _print_table(columns: list[str], rows: list, out=sys.stdout) -> None:
    if not rows:
        print("(no rows returned)", file=out)
        return
    widths = [len(c) for c in columns]
    str_rows: list[list[str]] = []
    for row in rows:
        sr = ["NULL" if v is None else str(v) for v in row]
        str_rows.append(sr)
        for i, v in enumerate(sr):
            widths[i] = max(widths[i], len(v))
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    hdr = "|" + "|".join(f" {c:<{w}} " for c, w in zip(columns, widths)) + "|"
    print(sep, file=out)
    print(hdr, file=out)
    print(sep, file=out)
    for sr in str_rows:
        print("|" + "|".join(f" {v:<{w}} " for v, w in zip(sr, widths)) + "|", file=out)
    print(sep, file=out)
    n = len(rows)
    print(f"({n} row{'s' if n != 1 else ''})", file=out)


def _print_csv(columns: list[str], rows: list, out=sys.stdout) -> None:
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["NULL" if v is None else v for v in row])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run SQL on Oracle via port-forwarded localhost.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "sql", nargs="?",
        help="SQL statement to execute (or pipe via stdin)",
    )
    parser.add_argument(
        "--env", default="prod", choices=["prod", "test"],
        help="Target DB environment (default: prod)",
    )
    parser.add_argument(
        "--csv", dest="csv_out", action="store_true",
        help="Output as CSV instead of ASCII table",
    )
    parser.add_argument(
        "--list-schemas", action="store_true",
        help="List accessible schema owners",
    )
    args = parser.parse_args()

    sql = args.sql
    if not sql and not sys.stdin.isatty():
        sql = sys.stdin.read().strip()
    if args.list_schemas:
        sql = "SELECT DISTINCT OWNER FROM ALL_OBJECTS ORDER BY OWNER"
    if not sql:
        parser.print_help()
        sys.exit(1)

    mode = _init_oracle_client()
    print(f"[oracle_sql] env={args.env.upper()}  localhost:{_LOCAL_PORTS[args.env]}  mode={mode}", file=sys.stderr)

    try:
        con, dsn = _connect(args.env)
    except oracledb.DatabaseError as exc:
        sys.exit(f"Connection failed: {exc}")

    print(f"[oracle_sql] connected → {dsn}", file=sys.stderr)

    try:
        cur = con.cursor()
        cur.execute(sql)
        if cur.description is None:
            con.commit()
            print(f"OK — {cur.rowcount} row(s) affected.", file=sys.stderr)
        else:
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
            if args.csv_out:
                _print_csv(columns, rows)
            else:
                _print_table(columns, rows)
        cur.close()
    except oracledb.DatabaseError as exc:
        sys.exit(f"SQL error: {exc}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
