#!/usr/bin/env python3
"""Query the Specify 7 REST API via port-forwarded localhost:8000.

Requires: source scripts/port-forward.sh backend  first (or 'all').
  That forwards svc/specify7-backend → localhost:8000.

Usage:
  specify_api "/context/login/"                               # list available collections
  specify_api --collection O-V "/api/specify/taxon/?limit=5"  # GET any path, pretty JSON
  specify_api --collection O-V --text-fields                  # CollectionObject text-field labels
  specify_api --collection O-V --geography-tree               # GeographyTreeDef + Discipline link
  specify_api --collection O-V --purge-geography-treedef 2 --dry-run   # count API purge impact
  specify_api --collection O-V --purge-geography-all --yes            # DELETE all geography (REST)
  specify_api --collection O-V --json "/api/specify/collectionobject/?limit=2"  # raw JSON
  specify_api --collection O-V --table "/api/specify/agent/?limit=10"           # ASCII table

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
        # 1) exact name match, 2) prefix match (e.g. "O" → "O-V")
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


def _csrf(session: requests.Session) -> str:
    return session.cookies.get("csrftoken", "") or ""


def _put(session: requests.Session, base: str, path: str, body: dict) -> dict | list:
    url = f"{base}{path}" if path.startswith("/") else path
    r = session.put(
        url,
        json=body,
        headers={
            "X-CSRFToken": _csrf(session),
            "Referer": base,
            "Content-Type": "application/json",
        },
        timeout=120,
    )
    if not r.ok:
        sys.exit(f"PUT {path} → {r.status_code}\n{r.text[:800]}")
    if r.text:
        return r.json()
    return {}


def _delete(session: requests.Session, base: str, path: str) -> None:
    url = f"{base}{path}" if path.startswith("/") else path
    r = session.delete(
        url,
        headers={"X-CSRFToken": _csrf(session), "Referer": base},
        timeout=120,
    )
    if r.status_code not in (200, 204):
        sys.exit(f"DELETE {path} → {r.status_code}\n{r.text[:800]}")


def _post(session: requests.Session, base: str, path: str, body: dict) -> dict:
    url = f"{base}{path}" if path.startswith("/") else path
    r = session.post(
        url,
        json=body,
        headers={
            "X-CSRFToken": _csrf(session),
            "Referer": base,
            "Content-Type": "application/json",
        },
        timeout=120,
    )
    if r.status_code not in (200, 201):
        sys.exit(f"POST {path} → {r.status_code}\n{r.text[:800]}")
    if r.text:
        return r.json()
    return {}


def _resource_pk(uri: str | None) -> int | None:
    if not uri or not isinstance(uri, str):
        return None
    parts = uri.rstrip("/").split("/")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


def _iter_list_endpoint(session: requests.Session, base: str, query: str) -> list[dict]:
    """GET all pages from a ``/api/specify/<model>/`` list URL (uses ``meta.total_count`` when present)."""
    offset = 0
    limit = 300
    out: list[dict] = []
    total: int | None = None
    while True:
        sep = "&" if "?" in query else "?"
        path = f"{query}{sep}limit={limit}&offset={offset}"
        data = _get(session, base, path)
        if not isinstance(data, dict):
            break
        objs = data.get("objects") or []
        meta = data.get("meta") or {}
        if total is None and meta.get("total_count") is not None:
            total = int(meta["total_count"])
        out.extend(objs)
        offset += len(objs)
        if not objs:
            break
        if total is not None and offset >= total:
            break
        if len(objs) < limit:
            break
    return out


def _strip_meta_for_put(obj: dict) -> dict:
    """Remove read-only / response-only keys before PUT."""
    skip = frozenset({"resource_uri", "recordset_info", "_tableName"})
    return {k: v for k, v in obj.items() if k not in skip}


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


def _cmd_purge_geography_via_api(
    session: requests.Session,
    base: str,
    *,
    treedef_id: int | None,
    dry_run: bool,
    yes: bool,
) -> None:
    """Mirror ``flows.lib.specify_geography_purge`` using REST: null FKs, delete leaves-up, POST Earth.

    Uses ``domainfilter=false`` so Locality / Geography are not limited to the logged-in collection.
    Requires an account with permission to update/delete those rows (often institution admin).

    * ``treedef_id`` — if set, only rows with ``definition`` = that id; otherwise every treedef.

    Does **not** truncate ``migration_oracle_placemap`` (the Django purge helpers do). Clear that
    table separately if placemap rows must match a wiped geography tree.
    """
    if not dry_run and not yes:
        sys.exit(
            "Refusing destructive geography purge without --yes.\n"
            "  Add --dry-run to only print counts, or --yes to execute."
        )

    geo_query = "/api/specify/geography/?domainfilter=false"
    if treedef_id is not None:
        geo_query += f"&definition={int(treedef_id)}"

    geo_rows = _iter_list_endpoint(session, base, geo_query)
    target_ids = {int(o["id"]) for o in geo_rows if o.get("id") is not None}

    loc_rows = _iter_list_endpoint(
        session,
        base,
        "/api/specify/locality/?domainfilter=false&geography__isnull=false",
    )
    loc_touch = [o for o in loc_rows if _resource_pk(o.get("geography")) in target_ids]

    ag_rows = _iter_list_endpoint(session, base, "/api/specify/agentgeography/?domainfilter=false")
    ag_touch = [o for o in ag_rows if _resource_pk(o.get("geography")) in target_ids]

    acc_touch = [
        o
        for o in geo_rows
        if _resource_pk(o.get("acceptedgeography")) in target_ids
    ]

    print(
        json.dumps(
            {
                "dry_run": dry_run,
                "treedef_id": treedef_id,
                "geography_rows": len(geo_rows),
                "localities_to_null_geography": len(loc_touch),
                "agentgeography_to_delete": len(ag_touch),
                "geography_accepted_to_clear": len(acc_touch),
            },
            indent=2,
        )
    )

    if dry_run:
        return

    for o in loc_touch:
        lid = int(o["id"])
        full = _get(session, base, f"/api/specify/locality/{lid}/")
        body = _strip_meta_for_put(full)
        body["geography"] = None
        _put(session, base, f"/api/specify/locality/{lid}/", body)
        print(f"[purge] locality {lid}: geography nulled", file=sys.stderr)

    for o in ag_touch:
        aid = int(o["id"])
        _delete(session, base, f"/api/specify/agentgeography/{aid}/")
        print(f"[purge] agentgeography {aid} deleted", file=sys.stderr)

    for o in acc_touch:
        gid = int(o["id"])
        full = _get(session, base, f"/api/specify/geography/{gid}/")
        body = _strip_meta_for_put(full)
        body["acceptedgeography"] = None
        _put(session, base, f"/api/specify/geography/{gid}/", body)
        print(f"[purge] geography {gid}: acceptedgeography cleared", file=sys.stderr)

    deleted_geo = 0
    guard = 0
    while True:
        guard += 1
        if guard > 25000:
            sys.exit("purge geography: iteration guard exceeded (possible cycle or API error)")
        geo_now = _iter_list_endpoint(session, base, geo_query)
        if not geo_now:
            break
        parent_ids = {
            int(_resource_pk(x["parent"]))
            for x in geo_now
            if x.get("parent")
        }
        leaves = [x for x in geo_now if int(x["id"]) not in parent_ids]
        if not leaves:
            sys.exit("purge geography: no leaf nodes but geography rows remain — check tree integrity")
        for x in leaves:
            gid = int(x["id"])
            _delete(session, base, f"/api/specify/geography/{gid}/")
            deleted_geo += 1
        print(f"[purge] geography batch deleted={len(leaves)} total_deleted={deleted_geo}", file=sys.stderr)

    treedefs_to_seed: list[int]
    if treedef_id is not None:
        treedefs_to_seed = [int(treedef_id)]
    else:
        td_data = _get(session, base, "/api/specify/geographytreedef/?limit=500")
        treedefs_to_seed = sorted({int(x["id"]) for x in td_data.get("objects", []) if x.get("id") is not None})

    for tid in treedefs_to_seed:
        roots = _iter_list_endpoint(
            session,
            base,
            f"/api/specify/geographytreedefitem/?treedef={tid}&parent__isnull=true&orderby=rankid",
        )
        if not roots:
            print(f"[purge] skip Earth for treedef {tid} (no GeographyTreeDefItem rows)", file=sys.stderr)
            continue
        top = min(roots, key=lambda it: int(it.get("rankid") or 0))
        di_uri = top.get("resource_uri") or f"/api/specify/geographytreedefitem/{top['id']}/"
        rankid = int(top.get("rankid") or 0)
        guid = f"urn:migration:geography-root:treedef-{tid}"[:128]
        body = {
            "name": "Earth",
            "fullname": "Planet",
            "definition": f"/api/specify/geographytreedef/{tid}/",
            "definitionitem": di_uri,
            "parent": None,
            "rankid": rankid,
            "isaccepted": True,
            "iscurrent": True,
            "guid": guid,
        }
        created = _post(session, base, "/api/specify/geography/", body)
        print(
            f"[purge] Earth root POST treedef={tid} geography_id={created.get('id')} rankid={rankid}",
            file=sys.stderr,
        )

    print(json.dumps({"ok": True, "geography_deleted": deleted_geo, "earth_treedefs_seeded": treedefs_to_seed}, indent=2))


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
    parser.add_argument(
        "--purge-geography-all",
        action="store_true",
        help="DELETE every Geography row (all treedefs) via REST, null FKs, then POST one Earth per treedef",
    )
    parser.add_argument(
        "--purge-geography-treedef",
        type=int,
        metavar="ID",
        default=None,
        help="Same as --purge-geography-all but only for GeographyTreeDef ID (e.g. 2)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm destructive --purge-geography-* (not required with --dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --purge-geography-*: print counts only, no writes",
    )
    args = parser.parse_args()

    if args.purge_geography_all and args.purge_geography_treedef is not None:
        parser.error("Use only one of --purge-geography-all or --purge-geography-treedef")

    session, base = _login(args.collection)

    if args.purge_geography_all or args.purge_geography_treedef is not None:
        _cmd_purge_geography_via_api(
            session,
            base,
            treedef_id=args.purge_geography_treedef,
            dry_run=args.dry_run,
            yes=args.yes,
        )
        return

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
        sys.exit("Provide an API path, or use --purge-geography-all / --purge-geography-treedef / --help.")

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
