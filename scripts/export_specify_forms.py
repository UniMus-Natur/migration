#!/usr/bin/env python3
"""Export Specify form/view XML definitions to git-friendly files.

Reads credentials from `.env` (same keys as `example.env`):
  - SPECIFY7_URL
  - SPECIFY7_USER
  - SPECIFY7_PASSWORD

The script uses only the public Specify API (no ORM fallback), extracts view
definitions grouped by table+view-name, deduplicates by XML content hash, and writes:

  forms/<table>/<view_name>/default.xml
  forms/<table>/<view_name>/overrides/<discipline>.xml
  forms/<table>/<view_name>/manifest.json

Usage:
  python scripts/export_specify_forms.py
  python scripts/export_specify_forms.py --output-dir forms --clean
  python scripts/export_specify_forms.py --collection NHM
  python scripts/export_specify_forms.py --only-overrides
  python scripts/export_specify_forms.py --no-manifests
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    sys.exit("requests not found. Activate the project venv first:\n  source .venv/bin/activate")


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if key not in os.environ:
                os.environ[key] = val.strip()


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        sys.exit(f"Missing required env var: {name}")
    return value


def _safe_name(value: str) -> str:
    out = []
    for ch in value.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in (" ", "-", "_", "."):
            out.append("-")
        else:
            out.append("-")
    collapsed = "".join(out)
    while "--" in collapsed:
        collapsed = collapsed.replace("--", "-")
    return collapsed.strip("-") or "unknown"


def _resource_pk(uri: str | None) -> int | None:
    if not uri or not isinstance(uri, str):
        return None
    parts = uri.rstrip("/").split("/")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


def _iter_list_endpoint(session: requests.Session, base: str, query: str) -> list[dict[str, Any]]:
    offset = 0
    limit = 300
    out: list[dict[str, Any]] = []
    total: int | None = None
    while True:
        sep = "&" if "?" in query else "?"
        path = f"{query}{sep}limit={limit}&offset={offset}"
        url = f"{base}{path}" if path.startswith("/") else path
        res = session.get(url, timeout=60)
        if not res.ok:
            sys.exit(f"GET {path} failed ({res.status_code}): {res.text[:500]}")
        data = res.json()
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


def _login(base: str, user: str, password: str, collection_name: str | None) -> requests.Session:
    s = requests.Session()
    s.verify = False

    r = s.get(f"{base}/context/login/", timeout=20)
    if r.status_code != 200:
        sys.exit(f"GET /context/login/ failed ({r.status_code}): {r.text[:400]}")

    csrf = s.cookies.get("csrftoken", "")
    data = r.json()
    collections: dict[str, int] = data.get("collections", {})
    if not collections:
        sys.exit("No collections returned by /context/login/")

    if collection_name:
        needle = collection_name.lower()
        by_name = {k.lower(): (k, v) for k, v in collections.items()}
        if needle in by_name:
            selected_name, col_id = by_name[needle]
        else:
            prefix_hits = [(k, v) for kl, (k, v) in by_name.items() if kl.startswith(needle)]
            if len(prefix_hits) == 1:
                selected_name, col_id = prefix_hits[0]
            else:
                available = ", ".join(sorted(collections))
                sys.exit(f"Collection '{collection_name}' not found or ambiguous. Available: {available}")
    else:
        selected_name, col_id = sorted(collections.items(), key=lambda x: x[0])[0]

    auth = s.put(
        f"{base}/context/login/",
        json={"username": user, "password": password, "collection": col_id},
        headers={"X-CSRFToken": csrf, "Referer": base},
        timeout=20,
    )
    if auth.status_code not in (200, 204):
        sys.exit(f"Login failed ({auth.status_code}): {auth.text[:400]}")

    print(f"[export_specify_forms] logged in to {base} as {user}, collection={selected_name}", file=sys.stderr)
    return s


def _get_json(session: requests.Session, base: str, path: str) -> Any:
    url = f"{base}{path}" if path.startswith("/") else path
    res = session.get(url, timeout=60)
    if not res.ok:
        sys.exit(f"GET {path} failed ({res.status_code}): {res.text[:500]}")
    return res.json()


def _canonical_xml(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def _build_form_xml(
    viewset_name: str,
    view_elem: ET.Element,
    viewdefs: dict[str, str],
    *,
    viewset_source: str | None = None,
    viewset_level: str | None = None,
    viewset_file: str | None = None,
) -> str:
    attrs = {"name": viewset_name}
    if viewset_source:
        attrs["source"] = viewset_source
    if viewset_level:
        attrs["level"] = str(viewset_level)
    if viewset_file:
        attrs["file"] = viewset_file
    root = ET.Element("viewset", attrs)
    views = ET.SubElement(root, "views")
    views.append(view_elem)
    defs = ET.SubElement(root, "viewdefs")
    for name in sorted(viewdefs):
        try:
            defs.append(ET.fromstring(viewdefs[name]))
        except ET.ParseError:
            continue
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> None:
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Export Specify form/view XML definitions")
    parser.add_argument("--output-dir", default="forms", help="Output directory (default: forms)")
    parser.add_argument(
        "--collection",
        default=os.getenv("SPECIFY7_COLLECTION"),
        help="Collection name to use for login (default: SPECIFY7_COLLECTION)",
    )
    parser.add_argument("--clean", action="store_true", help="Delete output dir before writing")
    parser.add_argument(
        "--only-overrides",
        action="store_true",
        help="Write only overrides/* files + manifests (skip default.xml files)",
    )
    parser.add_argument(
        "--no-manifests",
        action="store_true",
        help="Skip writing per-form manifest.json files",
    )
    args = parser.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    _load_dotenv(scripts_dir.parent / ".env")

    base = _require_env("SPECIFY7_URL").rstrip("/")
    user = _require_env("SPECIFY7_USER")
    password = _require_env("SPECIFY7_PASSWORD")

    session = _login(base, user, password, args.collection)

    disciplines = _iter_list_endpoint(session, base, "/api/specify/discipline/?orderby=name")
    discipline_name_by_id = {
        int(d["id"]): (d.get("name") or f"discipline-{d['id']}")
        for d in disciplines
        if d.get("id") is not None
    }
    collections = _iter_list_endpoint(session, base, "/api/specify/collection/?limit=500")
    datamodel = _get_json(session, base, "/context/datamodel.json")
    models = sorted({
        str(entry.get("classname", "")).split(".")[-1].strip()
        for entry in datamodel
        if isinstance(entry, dict) and entry.get("classname")
    })
    if not models:
        sys.exit("No model class names found from /context/datamodel.json")

    # table -> view_name -> discipline(or None) -> list[xml]
    forms_by_table: dict[str, dict[str, dict[str | None, list[str]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    scanned_context_views = 0
    for col in collections:
        collection_id = col.get("id")
        if collection_id is None:
            continue
        disc_id = _resource_pk(col.get("discipline"))
        discipline_name = (
            discipline_name_by_id.get(disc_id, f"discipline-{disc_id}")
            if disc_id is not None
            else None
        )
        for model_name in models:
            path = (
                f"/context/views.json?table={quote(model_name)}&limit=0&collectionid={int(collection_id)}"
            )
            rows = _get_json(session, base, path)
            if not isinstance(rows, list) or not rows:
                continue
            for row in rows:
                view_xml = row.get("view")
                if not isinstance(view_xml, str) or not view_xml.strip():
                    continue
                try:
                    view_elem = ET.fromstring(view_xml)
                except ET.ParseError:
                    continue
                viewdefs = row.get("viewdefs") or {}
                if not isinstance(viewdefs, dict):
                    viewdefs = {}
                viewset_name = row.get("viewsetName") or "context-viewset"
                form_xml = _build_form_xml(
                    str(viewset_name),
                    view_elem,
                    {str(k): str(v) for k, v in viewdefs.items()},
                    viewset_source=str(row.get("viewsetSource") or ""),
                    viewset_level=str(row.get("viewsetLevel") or ""),
                    viewset_file=str(row.get("viewsetFile") or ""),
                )
                canonical = _canonical_xml(form_xml)
                view_name = _safe_name(str(row.get("name") or model_name))
                forms_by_table[_safe_name(model_name)][view_name][discipline_name].append(canonical)
                scanned_context_views += 1

    output_dir = Path(args.output_dir)
    if args.clean and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "source": base,
        "collections_scanned": len([c for c in collections if c.get("id") is not None]),
        "tables_scanned": len(models),
        "context_views_scanned": scanned_context_views,
        "forms": {},
    }

    for table in sorted(forms_by_table):
        for view_name in sorted(forms_by_table[table]):
            form_dir = output_dir / table / view_name
            overrides_dir = form_dir / "overrides"
            overrides_dir.mkdir(parents=True, exist_ok=True)

            # Collapse duplicates per discipline for this specific view name.
            disc_hash_to_xml: dict[str, tuple[str, str]] = {}
            all_hashes: list[str] = []
            for disc_name, xml_list in forms_by_table[table][view_name].items():
                if not xml_list:
                    continue
                counter = Counter(_hash_text(x) for x in xml_list)
                chosen_hash, _ = counter.most_common(1)[0]
                chosen_xml = next(x for x in xml_list if _hash_text(x) == chosen_hash)
                key = "__default__" if disc_name is None else disc_name
                disc_hash_to_xml[key] = (chosen_hash, chosen_xml)
                all_hashes.append(chosen_hash)

            if not disc_hash_to_xml:
                continue

            if "__default__" in disc_hash_to_xml:
                default_hash = disc_hash_to_xml["__default__"][0]
            else:
                default_hash = Counter(all_hashes).most_common(1)[0][0]

            default_xml = next(xml for h, xml in disc_hash_to_xml.values() if h == default_hash)
            if not args.only_overrides:
                (form_dir / "default.xml").write_text(default_xml, encoding="utf-8")

            manifest: dict[str, Any] = {
                "table": table,
                "view_name": view_name,
                "default_hash": default_hash,
                "default_file": None if args.only_overrides else "default.xml",
                "disciplines": {},
            }
            override_count = 0
            for disc_key in sorted(disc_hash_to_xml):
                if disc_key == "__default__":
                    continue
                hsh, xml = disc_hash_to_xml[disc_key]
                if hsh == default_hash:
                    manifest["disciplines"][disc_key] = {
                        "kind": "default",
                        "hash": hsh,
                        "file": None if args.only_overrides else "default.xml",
                    }
                    continue
                filename = f"{_safe_name(disc_key)}.xml"
                rel = f"overrides/{filename}"
                (overrides_dir / filename).write_text(xml, encoding="utf-8")
                manifest["disciplines"][disc_key] = {"kind": "override", "hash": hsh, "file": rel}
                override_count += 1

            if "__default__" in disc_hash_to_xml:
                d_hash, _ = disc_hash_to_xml["__default__"]
                manifest["default_context"] = {
                    "kind": "common",
                    "hash": d_hash,
                    "file": None if args.only_overrides else "default.xml",
                }

            if not args.no_manifests:
                (form_dir / "manifest.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            summary["forms"][f"{table}/{view_name}"] = {
                "disciplines_seen": len([k for k in disc_hash_to_xml if k != "__default__"]),
                "overrides_written": override_count,
                "unique_variants": len({h for h, _ in disc_hash_to_xml.values()}),
            }

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        f"[export_specify_forms] done: context_views={scanned_context_views}, forms={len(summary['forms'])}, "
        f"output={output_dir}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
