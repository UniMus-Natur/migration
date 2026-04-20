#!/usr/bin/env python3
"""Query the Specify 7 REST API via port-forwarded localhost:8000.

Requires: source scripts/port-forward.sh backend  first (or 'all').
  That forwards svc/specify7-backend → localhost:8000.

Usage:
  specify_api "/context/login/"                               # list available collections
  specify_api --collection NHM "/api/specify/taxon/?limit=5"  # GET any path, pretty JSON
  specify_api --collection NHM --text-fields                  # CollectionObject text-field labels
  specify_api --collection NHM --geography-tree               # GeographyTreeDef + Discipline link
  specify_api --collection NHM --json "/api/specify/collectionobject/?limit=2"  # raw JSON
  specify_api --collection NHM --table "/api/specify/agent/?limit=10"           # ASCII table

Env vars (read from .env if not already exported):
  SPECIFY7_URL         Base URL (default: http://localhost:8000)
  SPECIFY7_USER        Username
  SPECIFY7_PASSWORD    Password
  SPECIFY7_COLLECTION  Default collection name (e.g. NHM); overridden by --collection
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env
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
# requests import
# ---------------------------------------------------------------------------

try:
    import requests
except ImportError:
    sys.exit("requests not found. Activate the project venv first:\n  source .venv/bin/activate")

# ---------------------------------------------------------------------------
# Session / auth
# ---------------------------------------------------------------------------

def _base_url() -> str:
    url = os.getenv("SPECIFY7_URL", "").rstrip("/")
    if not url:
        sys.exit("SPECIFY7_URL is not set. Add it to .env (see example.env).")
    return url


def _credentials() -> tuple[str, str]:
    user = os.getenv("SPECIFY7_USER", "")
    pw = os.getenv("SPECIFY7_PASSWORD", "")
    missing = [k for k, v in {"SPECIFY7_USER": user, "SPECIFY7_PASSWORD": pw}.items() if not v]
    if missing:
        sys.exit(
            f"Missing env vars: {', '.join(missing)}\n"
            "  Load your .env or export the variables."
        )
    return user, pw


def _login(collection_name: str | None) -> tuple[requests.Session, str]:
    """Create an authenticated session.  Returns (session, base_url).

    If *collection_name* is None, log in to the first available collection
    (useful for read-only context queries)."""
    base = _base_url()
    user, pw = _credentials()

    s = requests.Session()
    s.verify = False  # staging may use a self-signed cert

    # Step 1 — GET /context/login/ to discover collections + CSRF cookie
    r = s.get(f"{base}/context/login/", timeout=15)
    if r.status_code != 200:
        sys.exit(f"GET /context/login/ failed: {r.status_code}\n{r.text[:400]}")

    csrf = s.cookies.get("csrftoken", "")
    login_data = r.json()
    collections: dict[str, int] = login_data.get("collections", {})

    if not collections:
        sys.exit("No collections returned by /context/login/.")

    if collection_name is None:
        col_id = next(iter(sorted(collections.values())))
        chosen = next(k for k, v in collections.items() if v == col_id)
    else:
        needle = collection_name.lower()
        # 1) exact name match, 2) prefix match (e.g. "NHM" → "NHM-karplanter")
        by_name = {k.lower(): (k, v) for k, v in collections.items()}
        if needle in by_name:
            chosen, col_id = by_name[needle]
        else:
            prefix_hits = [(k, v) for kl, (k, v) in by_name.items() if kl.startswith(needle)]
            if len(prefix_hits) == 1:
                chosen, col_id = prefix_hits[0]
            elif len(prefix_hits) > 1:
                candidates = ", ".join(k for k, _ in prefix_hits)
                sys.exit(
                    f"'{collection_name}' is ambiguous — matches: {candidates}\n"
                    "  Use a more specific name."
                )
            else:
                available = ", ".join(sorted(collections.keys()))
                sys.exit(
                    f"Collection '{collection_name}' not found.\n"
                    f"  Available: {available}"
                )

    print(f"[specify_api] {base}  collection='{chosen}' (id={col_id})", file=sys.stderr)

    # Step 2 — PUT /context/login/ to authenticate
    r = s.put(
        f"{base}/context/login/",
        json={"username": user, "password": pw, "collection": col_id},
        headers={"X-CSRFToken": csrf, "Referer": base},
        timeout=15,
    )
    if r.status_code not in (200, 204):
        sys.exit(f"Login failed ({r.status_code}): {r.text[:400]}")

    return s, base


def _get(session: requests.Session, base: str, path: str) -> dict | list:
    url = f"{base}{path}" if path.startswith("/") else path
    r = session.get(url, timeout=30)
    if not r.ok:
        sys.exit(f"GET {path} → {r.status_code}\n{r.text[:600]}")
    return r.json()


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def _print_table(data: dict | list, out=sys.stdout) -> None:
    """Print a Specify list-resource response as an ASCII table.
    Handles both raw lists and Specify envelope ``{"objects": [...], "meta": {...}}``."""
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("objects", [data])
    else:
        print(json.dumps(data, indent=2, default=str), file=out)
        return

    if not rows:
        print("(no rows returned)", file=out)
        return

    # Collect all keys across rows
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            for k in row:
                if k not in seen:
                    columns.append(k)
                    seen.add(k)

    if not columns:
        print(json.dumps(rows, indent=2, default=str), file=out)
        return

    widths = [len(c) for c in columns]
    str_rows: list[list[str]] = []
    for row in rows:
        sr = []
        for i, col in enumerate(columns):
            raw = row.get(col) if isinstance(row, dict) else ""
            v = "NULL" if raw is None else str(raw)
            if len(v) > 80:
                v = v[:77] + "..."
            sr.append(v)
            widths[i] = max(widths[i], len(v))
        str_rows.append(sr)

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

    if isinstance(data, dict) and "meta" in data:
        meta = data["meta"]
        print(
            f"  total={meta.get('total_count', '?')}  "
            f"offset={meta.get('offset', 0)}  "
            f"limit={meta.get('limit', '?')}",
            file=out,
        )


def _print_csv_data(data: dict | list, out=sys.stdout) -> None:
    rows = data.get("objects", [data]) if isinstance(data, dict) else data
    if not rows:
        return
    columns = list(rows[0].keys()) if rows and isinstance(rows[0], dict) else []
    writer = csv.writer(out)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") for c in columns])


# ---------------------------------------------------------------------------
# Built-in diagnostic queries
# ---------------------------------------------------------------------------

def _cmd_list_collections(session: requests.Session, base: str) -> None:
    """Print all Specify collections visible to the logged-in user."""
    data = _get(session, base, "/api/specify/collection/?limit=100")
    _print_table(data)


def _cmd_text_fields(session: requests.Session, base: str) -> None:
    """Show the label/caption of all text1-text8 fields on CollectionObject
    by querying SpLocaleContainerItem for the active discipline."""
    data = _get(session, base, "/api/specify/splocalecontainer/?name=collectionobject&limit=10")
    containers = data.get("objects", [])
    if not containers:
        print("No splocalecontainer named 'collectionobject' found.", file=sys.stderr)
        return

    results = []
    for container in containers:
        cid = container["id"]
        items_data = _get(
            session, base,
            f"/api/specify/splocalecontaineritem/"
            f"?container={cid}&name__in=text1,text2,text3,text4,text5,text6,text7,text8"
            f"&limit=50",
        )
        for item in items_data.get("objects", []):
            results.append({
                "container_id": cid,
                "container_schema": container.get("schematype", ""),
                "field_name": item.get("name", ""),
                "is_hidden": item.get("ishidden", ""),
                "is_required": item.get("isrequired", ""),
                "format": item.get("format", ""),
                "resource_uri": item.get("resource_uri", ""),
            })

    if not results:
        print("No text1-text8 items found — falling back to all items in container.", file=sys.stderr)
        for container in containers:
            cid = container["id"]
            all_items = _get(session, base, f"/api/specify/splocalecontaineritem/?container={cid}&limit=200")
            print(f"\n--- all items in container id={cid} ---")
            _print_table(all_items)
        return

    # Enrich with container discipline info
    container_ids = list({r["container_id"] for r in results})
    container_info: dict[int, str] = {}
    for cid in container_ids:
        cdata = _get(session, base, f"/api/specify/splocalecontainer/{cid}/")
        disc_uri = cdata.get("discipline", "") or ""
        container_info[cid] = disc_uri  # e.g. "/api/specify/discipline/9/"

    for r in results:
        r["discipline_uri"] = container_info.get(r["container_id"], "")

    _print_table(results)

    # Also show locale names (labels) for those items
    # SpLocaleItemStr filters: itemname=<item_id> (for the "name" string of a ContainerItem)
    print("\n--- Locale labels ---")
    for item in results:
        uri = item["resource_uri"]
        item_id = uri.strip("/").split("/")[-1]
        names_data = _get(session, base, f"/api/specify/splocaleitemstr/?itemname={item_id}&limit=20")
        for lbl in names_data.get("objects", []):
            print(
                f"  container={item['container_id']}  field={item['field_name']}"
                f"  lang={lbl.get('language','')}  country={lbl.get('country','')}"
                f"  text={lbl.get('text','')}",
            )


def _cmd_geography_tree(session: requests.Session, base: str) -> None:
    """Show GeographyTreeDef records and whether disciplines are linked to one."""
    print("=== GeographyTreeDef ===")
    _print_table(_get(session, base, "/api/specify/geographytreedef/?limit=50"))

    print("\n=== Discipline.geographytreedef ===")
    disc_data = _get(session, base, "/api/specify/discipline/?limit=50")
    summary = [
        {
            "id": d.get("id"),
            "name": d.get("name"),
            "type": d.get("type"),
            "geographytreedef": d.get("geographytreedef"),
        }
        for d in disc_data.get("objects", [])
    ]
    _print_table(summary)

    print("\n=== GeographyTreeDefItem (levels) ===")
    _print_table(_get(session, base, "/api/specify/geographytreedefitem/?limit=100"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # Suppress InsecureRequestWarning for self-signed certs on staging
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description="Query the Specify 7 REST API via port-forwarded localhost.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "path", nargs="?",
        help="API path to GET (e.g. /api/specify/taxon/?limit=5)",
    )
    parser.add_argument(
        "--collection", "-c",
        default=os.getenv("SPECIFY7_COLLECTION"),
        help="Collection name to log in to (default: SPECIFY7_COLLECTION env var)",
    )
    parser.add_argument(
        "--json", dest="raw_json", action="store_true",
        help="Output raw pretty-printed JSON (default: ASCII table for list endpoints)",
    )
    parser.add_argument(
        "--csv", dest="csv_out", action="store_true",
        help="Output as CSV",
    )
    parser.add_argument(
        "--text-fields", action="store_true",
        help="Show CollectionObject text1-text8 field labels from SpLocaleContainerItem",
    )
    parser.add_argument(
        "--geography-tree", action="store_true",
        help="Show GeographyTreeDef records and Discipline links",
    )
    parser.add_argument(
        "--list-collections", action="store_true",
        help="List all collections visible to the logged-in user",
    )
    args = parser.parse_args()

    session, base = _login(args.collection)

    if args.list_collections:
        _cmd_list_collections(session, base)
        return

    if args.text_fields:
        _cmd_text_fields(session, base)
        return

    if args.geography_tree:
        _cmd_geography_tree(session, base)
        return

    if not args.path:
        parser.print_help()
        sys.exit(1)

    data = _get(session, base, args.path)

    if args.raw_json or args.csv_out:
        if args.csv_out:
            _print_csv_data(data)
        else:
            print(json.dumps(data, indent=2, default=str))
    else:
        # Auto-detect: use table for list resources, pretty JSON for singletons
        if isinstance(data, dict) and "objects" in data:
            _print_table(data)
        elif isinstance(data, list):
            _print_table(data)
        else:
            print(json.dumps(data, indent=2, default=str))


if __name__ == "__main__":
    main()
