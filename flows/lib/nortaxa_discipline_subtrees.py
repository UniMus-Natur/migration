"""NorTaxa (Artsnavnebase) DwC-A: download, unpack, and slice ``taxon.txt`` per Specify discipline.

Slicing uses ``parentNameUsageID`` / ``taxonID`` edges from the DwC core. Root ``taxonID`` values
per discipline name are defined in ``flows.lib.nortaxa_discipline_root_specs``.

When ``vernacularname.txt`` is present, the same per-discipline ``taxonID`` keep-set is applied to
vernacular rows: the extension ``id`` column is the DwC **coreid** (here identical to ``taxonID``
for every core row in export v1.270; we also match against core ``id`` from sliced taxon rows).
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import urllib.request
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from xml.etree import ElementTree as ET

from flows.lib.nortaxa_discipline_root_specs import NorTaxaSliceSpec

ARTSNAVNEBASE_ARCHIVE_URL = "https://ipt.artsdatabanken.no/archive.do?r=artsnavnebase"


def _ensure_csv_field_limit() -> None:
    csv.field_size_limit(min(2**31 - 1, max(csv.field_size_limit(), 8_000_000)))


def download_archive(url: str, dest_zip: Path, *, timeout_s: int = 600) -> Path:
    """Download the DwC-A zip to ``dest_zip`` (parent dirs created)."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "migration-prefect-nortaxa/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp, dest_zip.open("wb") as out:
        shutil.copyfileobj(resp, out)
    return dest_zip


def _find_dwca_root(extracted_dir: Path) -> Path:
    if (extracted_dir / "meta.xml").is_file():
        return extracted_dir
    subs = [p for p in extracted_dir.iterdir() if p.is_dir()]
    if len(subs) == 1 and (subs[0] / "meta.xml").is_file():
        return subs[0]
    for p in extracted_dir.rglob("meta.xml"):
        return p.parent
    raise FileNotFoundError(f"No meta.xml under {extracted_dir}")


def extract_dwca_zip(zip_path: Path, dest_dir: Path) -> Path:
    """Extract zip into ``dest_dir`` and return the directory that contains ``meta.xml``."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    return _find_dwca_root(dest_dir)


def read_taxon_rows(taxon_txt: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    """Parse ``taxon.txt`` (tab-separated with header)."""
    _ensure_csv_field_limit()
    by_id: dict[str, dict[str, str]] = {}
    rows: list[dict[str, str]] = []
    with taxon_txt.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for raw in reader:
            tid = (raw.get("taxonID") or "").strip()
            if not tid:
                continue
            rows.append(raw)
            by_id[tid] = raw
    return rows, by_id


def _children_map(rows: Iterable[dict[str, str]]) -> dict[str, list[str]]:
    ch: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        tid = (r.get("taxonID") or "").strip()
        pid = (r.get("parentNameUsageID") or "").strip()
        if tid and pid:
            ch[pid].append(tid)
    return ch


def subtree_taxon_ids(root_ids: Iterable[str], children_by_parent: Mapping[str, list[str]]) -> set[str]:
    """All ``taxonID`` values reachable from ``root_ids`` following parent → child edges."""
    roots = {str(x).strip() for x in root_ids if str(x).strip()}
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        tid = stack.pop()
        if tid in seen:
            continue
        seen.add(tid)
        for c in children_by_parent.get(tid, ()):
            if c not in seen:
                stack.append(c)
    return seen


def compute_keep_ids(
    children_by_parent: Mapping[str, list[str]],
    spec: NorTaxaSliceSpec,
) -> set[str]:
    """Union of subtrees for ``root_taxon_ids``, minus subtrees for ``subtract_subtree_taxon_ids``."""
    keep: set[str] = set()
    for r in spec.root_taxon_ids:
        rid = str(r).strip()
        if rid:
            keep |= subtree_taxon_ids([rid], children_by_parent)
    for s in spec.subtract_subtree_taxon_ids:
        sid = str(s).strip()
        if sid:
            keep -= subtree_taxon_ids([sid], children_by_parent)
    return keep


def _label(by_id: Mapping[str, dict[str, str]], tid: str) -> dict[str, str]:
    r = by_id.get(tid, {})
    return {
        "taxonID": tid,
        "scientificName": r.get("scientificName", ""),
        "taxonRank": r.get("taxonRank", ""),
    }


def manifest_roots_for_spec(by_id: Mapping[str, dict[str, str]], spec: NorTaxaSliceSpec) -> list[dict]:
    roots = [_label(by_id, r) for r in spec.root_taxon_ids]
    if spec.subtract_subtree_taxon_ids:
        return [
            {
                "roots": roots,
                "subtract_subtrees": [_label(by_id, s) for s in spec.subtract_subtree_taxon_ids],
            }
        ]
    return roots


def missing_root_warnings(by_id: Mapping[str, dict[str, str]], spec: NorTaxaSliceSpec) -> list[str]:
    out: list[str] = []
    for r in spec.root_taxon_ids + spec.subtract_subtree_taxon_ids:
        if str(r).strip() and str(r).strip() not in by_id:
            out.append(f"taxonID {r} not present in DwC-A taxon.txt")
    return out


def filter_taxon_rows(rows: list[dict[str, str]], keep_ids: set[str]) -> list[dict[str, str]]:
    return [r for r in rows if (r.get("taxonID") or "").strip() in keep_ids]


def write_taxon_tsv(rows: list[dict[str, str]], dest: Path, fieldnames: list[str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def read_taxon_fieldnames(taxon_txt: Path) -> list[str]:
    with taxon_txt.open(encoding="utf-8", newline="") as fh:
        header = fh.readline().rstrip("\n")
    return header.split("\t")


def read_vernacular_rows(vernacular_txt: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Parse ``vernacularname.txt`` (tab-separated). First column is DwC **coreid** (``id``)."""
    _ensure_csv_field_limit()
    with vernacular_txt.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def core_ids_for_vernacular_join(taxon_subset: list[dict[str, str]]) -> set[str]:
    """Values in ``vernacularname`` column ``id`` that link to sliced taxon core rows."""
    out: set[str] = set()
    for r in taxon_subset:
        tid = (r.get("taxonID") or "").strip()
        cid = (r.get("id") or "").strip()
        if tid:
            out.add(tid)
        if cid:
            out.add(cid)
    return out


def filter_vernacular_rows(
    rows: list[dict[str, str]],
    core_ids: set[str],
) -> list[dict[str, str]]:
    return [r for r in rows if (r.get("id") or "").strip() in core_ids]


def package_title_from_eml(eml_xml: Path) -> str | None:
    if not eml_xml.is_file():
        return None
    try:
        tree = ET.parse(eml_xml)
        root = tree.getroot()
        for path in (".//title", ".//packageId", ".//shortName"):
            el = root.find(path)
            if el is not None and el.text:
                return el.text.strip()
    except ET.ParseError:
        return None
    return None


_SAFE_SLUG = re.compile(r"[^a-zA-Z0-9_-]+")


def discipline_artifact_basename(discipline_id: int, discipline_name: str | None) -> str:
    base = (discipline_name or "discipline").strip().lower().replace(" ", "_")
    base = _SAFE_SLUG.sub("_", base).strip("_") or "discipline"
    return f"taxon-discipline-{discipline_id}_{base}"


def run_slice_for_specify_disciplines(
    *,
    dwca_dir: Path,
    out_dir: Path,
    discipline_jobs: list[
        tuple[int, str | None, int, str | None, NorTaxaSliceSpec]
    ],
) -> dict:
    """Write one TSV per job ``(discipline_id, discipline_name, division_id, division_name, spec)``.

    ``discipline_jobs`` must only contain rows that have a concrete :class:`NorTaxaSliceSpec`.
    """
    taxon_txt = dwca_dir / "taxon.txt"
    if not taxon_txt.is_file():
        raise FileNotFoundError(taxon_txt)
    fieldnames = read_taxon_fieldnames(taxon_txt)
    rows, by_id = read_taxon_rows(taxon_txt)
    children = _children_map(rows)
    eml_title = package_title_from_eml(dwca_dir / "eml.xml")

    vernacular_path = dwca_dir / "vernacularname.txt"
    vernacular_fieldnames: list[str] = []
    vernacular_rows: list[dict[str, str]] = []
    if vernacular_path.is_file():
        vernacular_fieldnames, vernacular_rows = read_vernacular_rows(vernacular_path)

    artifacts: list[dict] = []
    for did, dname, divid, divname, spec in discipline_jobs:
        warns = missing_root_warnings(by_id, spec)
        keep = compute_keep_ids(children, spec)
        subset = filter_taxon_rows(rows, keep)
        base = discipline_artifact_basename(did, dname)
        fname = f"{base}.tsv"
        out_path = out_dir / fname
        write_taxon_tsv(subset, out_path, fieldnames)

        entry: dict = {
            "discipline_id": did,
            "discipline_name": dname,
            "division_id": divid,
            "division_name": divname,
            "artifact": fname,
            "rows": len(subset),
            "roots": manifest_roots_for_spec(by_id, spec),
            "root_taxon_ids": list(spec.root_taxon_ids),
            "subtract_subtree_taxon_ids": list(spec.subtract_subtree_taxon_ids),
            "missing_root_warnings": warns,
        }
        if vernacular_fieldnames:
            core_ids = core_ids_for_vernacular_join(subset)
            vsub = filter_vernacular_rows(vernacular_rows, core_ids)
            vname = f"{base}_vernacularname.tsv"
            write_taxon_tsv(vsub, out_dir / vname, vernacular_fieldnames)
            entry["vernacular_artifact"] = vname
            entry["vernacular_rows"] = len(vsub)
        artifacts.append(entry)

    manifest = {
        "source": str(ARTSNAVNEBASE_ARCHIVE_URL),
        "dwca_dir": str(dwca_dir),
        "eml_title_or_package": eml_title,
        "artifacts": artifacts,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
