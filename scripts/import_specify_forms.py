#!/usr/bin/env python3
"""Import git-tracked Specify form XML files back into Specify.

Reads form XML files produced by `export_specify_forms.py` and patches the
target viewset XML stored in `spappresourcedata`.

By default this script is a dry run. Use `--apply` to write changes.

Usage:
  python3 scripts/import_specify_forms.py --forms-dir forms
  python3 scripts/import_specify_forms.py --forms-dir forms_all --apply
  python3 scripts/import_specify_forms.py --forms-dir forms_admin_only --apply
  python3 scripts/import_specify_forms.py --forms-dir forms --viewset-name "Karplaner - standard"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
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


def _resource_pk(uri: str | None) -> int | None:
    if not uri or not isinstance(uri, str):
        return None
    parts = uri.rstrip("/").split("/")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


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

    print(f"[import_specify_forms] logged in to {base} as {user}, collection={selected_name}", file=sys.stderr)
    return s


def _strip_meta_for_put(obj: dict[str, Any]) -> dict[str, Any]:
    skip = frozenset({"resource_uri", "recordset_info", "_tableName"})
    return {k: v for k, v in obj.items() if k not in skip}


def _parse_local_form(path: Path) -> tuple[ET.Element, dict[str, ET.Element]]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    views = root.find("views")
    if views is None:
        raise ValueError(f"{path}: missing <views>")
    local_view = views.find("view")
    if local_view is None:
        raise ValueError(f"{path}: missing <views>/<view>")
    viewdefs_root = root.find("viewdefs")
    local_defs: dict[str, ET.Element] = {}
    if viewdefs_root is not None:
        for d in viewdefs_root:
            d_name = d.attrib.get("name")
            if d_name:
                local_defs[d_name] = ET.fromstring(ET.tostring(d, encoding="unicode"))
    local_view_copy = ET.fromstring(ET.tostring(local_view, encoding="unicode"))
    return local_view_copy, local_defs


def _gather_local_forms(forms_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for p in sorted(forms_dir.glob("*/*/*.xml")):
        if p.name == "manifest.xml":
            continue
        if "overrides" in p.parts:
            continue
        candidates.append(p)
    if not candidates:
        sys.exit(f"No form XML files found at {forms_dir} (expected */*/*.xml, excluding overrides)")
    return candidates


def _discover_viewset_name(session: requests.Session, base: str) -> str:
    # Use a common table that almost always exists in views context.
    rows = session.get(f"{base}/context/views.json?table=CollectionObject&limit=0", timeout=60).json()
    if not isinstance(rows, list) or not rows:
        sys.exit("Could not discover default viewset name from /context/views.json")
    name = str(rows[0].get("viewsetName") or "").strip()
    if not name:
        sys.exit("Discovered empty viewsetName from /context/views.json")
    return name


def _load_target_viewset(
    session: requests.Session,
    base: str,
    viewset_name: str,
) -> tuple[dict[str, Any], ET.Element]:
    rows = _iter_list_endpoint(session, base, f"/api/specify/spviewsetobj/?name={viewset_name}")
    if not rows:
        sys.exit(f"No spviewsetobj found with name '{viewset_name}'")
    if len(rows) > 1:
        sys.exit(f"Multiple spviewsetobj rows found for name '{viewset_name}', aborting")
    viewset_obj = rows[0]
    vid = int(viewset_obj["id"])
    data_rows = _iter_list_endpoint(session, base, f"/api/specify/spappresourcedata/?spviewsetobj__id={vid}")
    if not data_rows:
        sys.exit(f"No spappresourcedata found for viewset '{viewset_name}' (id={vid})")
    if len(data_rows) > 1:
        sys.exit(f"Multiple spappresourcedata rows found for viewset '{viewset_name}', aborting")
    data_obj = data_rows[0]
    xml_text = str(data_obj.get("data") or "")
    if not xml_text.strip():
        sys.exit(f"spappresourcedata for '{viewset_name}' is empty")
    root = ET.fromstring(xml_text)
    return data_obj, root


def _sync_forms_into_viewset(
    root: ET.Element, local_form_paths: list[Path], *, verbose_missing: bool
) -> dict[str, int]:
    views_root = root.find("views")
    if views_root is None:
        sys.exit("Target viewset XML missing <views>")
    defs_root = root.find("viewdefs")
    if defs_root is None:
        defs_root = ET.SubElement(root, "viewdefs")

    remote_by_key: dict[tuple[str, str], ET.Element] = {}
    for v in views_root.findall("view"):
        key = (v.attrib.get("name", ""), v.attrib.get("class", ""))
        remote_by_key[key] = v

    defs_by_name: dict[str, ET.Element] = {}
    for d in defs_root:
        d_name = d.attrib.get("name")
        if d_name:
            defs_by_name[d_name] = d

    matched = 0
    changed_views = 0
    changed_defs = 0
    missing = 0

    for path in local_form_paths:
        local_view, local_defs = _parse_local_form(path)
        key = (local_view.attrib.get("name", ""), local_view.attrib.get("class", ""))
        remote = remote_by_key.get(key)
        if remote is None:
            missing += 1
            if verbose_missing:
                print(f"[import_specify_forms] missing remote view for {path} key={key}", file=sys.stderr)
            continue
        matched += 1

        old_view = ET.tostring(remote, encoding="unicode")
        new_view = ET.tostring(local_view, encoding="unicode")
        if old_view != new_view:
            idx = list(views_root).index(remote)
            views_root.remove(remote)
            views_root.insert(idx, local_view)
            remote_by_key[key] = local_view
            changed_views += 1

        for d_name, local_def in local_defs.items():
            existing = defs_by_name.get(d_name)
            if existing is None:
                defs_root.append(local_def)
                defs_by_name[d_name] = local_def
                changed_defs += 1
                continue
            old_def = ET.tostring(existing, encoding="unicode")
            new_def = ET.tostring(local_def, encoding="unicode")
            if old_def != new_def:
                idx = list(defs_root).index(existing)
                defs_root.remove(existing)
                defs_root.insert(idx, local_def)
                defs_by_name[d_name] = local_def
                changed_defs += 1

    return {
        "matched_forms": matched,
        "missing_forms": missing,
        "changed_views": changed_views,
        "changed_viewdefs": changed_defs,
    }


def main() -> None:
    try:
        import urllib3

        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Import git-tracked Specify form XML files")
    parser.add_argument("--forms-dir", default="forms", help="Directory containing exported forms")
    parser.add_argument(
        "--collection",
        default=os.getenv("SPECIFY7_COLLECTION"),
        help="Collection name to use for login (default: SPECIFY7_COLLECTION)",
    )
    parser.add_argument(
        "--viewset-name",
        default=None,
        help="Specify viewset name (default: auto-discover from /context/views.json)",
    )
    parser.add_argument(
        "--backup",
        default=None,
        help="Optional file path to write current remote viewset XML before applying",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes to Specify (default is dry run)",
    )
    parser.add_argument(
        "--verbose-missing",
        action="store_true",
        help="Print each local XML that does not map to a view in the target viewset",
    )
    args = parser.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    _load_dotenv(scripts_dir.parent / ".env")

    base = _require_env("SPECIFY7_URL").rstrip("/")
    user = _require_env("SPECIFY7_USER")
    password = _require_env("SPECIFY7_PASSWORD")

    forms_dir = Path(args.forms_dir)
    if not forms_dir.exists():
        sys.exit(f"Forms directory does not exist: {forms_dir}")
    local_form_paths = _gather_local_forms(forms_dir)

    session = _login(base, user, password, args.collection)
    viewset_name = args.viewset_name or _discover_viewset_name(session, base)
    print(f"[import_specify_forms] target viewset: {viewset_name}", file=sys.stderr)

    data_obj, root = _load_target_viewset(session, base, viewset_name)
    before_xml = ET.tostring(root, encoding="unicode")
    if args.backup:
        backup_path = Path(args.backup)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(before_xml, encoding="utf-8")

    stats = _sync_forms_into_viewset(root, local_form_paths, verbose_missing=args.verbose_missing)
    ET.indent(root, space="  ")
    after_xml = ET.tostring(root, encoding="unicode")
    changed = before_xml != after_xml

    summary = {
        "forms_dir": str(forms_dir),
        "viewset_name": viewset_name,
        "dry_run": not args.apply,
        "xml_changed": changed,
        **stats,
    }

    if args.apply and changed:
        rid = int(data_obj["id"])
        full = session.get(f"{base}/api/specify/spappresourcedata/{rid}/", timeout=60).json()
        body = _strip_meta_for_put(full)
        body["data"] = after_xml
        res = session.put(
            f"{base}/api/specify/spappresourcedata/{rid}/",
            json=body,
            headers={
                "X-CSRFToken": session.cookies.get("csrftoken", ""),
                "Referer": base,
                "Content-Type": "application/json",
            },
            timeout=120,
        )
        if not res.ok:
            sys.exit(f"PUT spappresourcedata/{rid} failed ({res.status_code}): {res.text[:800]}")
        summary["applied"] = True
        summary["spappresourcedata_id"] = rid
    else:
        summary["applied"] = False

    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
