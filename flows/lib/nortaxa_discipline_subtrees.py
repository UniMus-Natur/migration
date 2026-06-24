"""NorTaxa API export per Specify discipline.

Uses ``DataTransfer/Export`` per root ``scientificNameId`` from
``flows.lib.nortaxa_discipline_root_specs``.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from flows.lib.nortaxa_api_client import (
    NORTAXA_TSV_FIELDNAMES,
    NorTaxaApiClient,
    row_scientific_name_id,
)
from flows.lib.nortaxa_discipline_root_specs import NorTaxaSliceSpec

NORTAXA_API_BASE_URL = "https://nortaxa.artsdatabanken.no"

_SAFE_SLUG = re.compile(r"[^a-zA-Z0-9_-]+")


def write_taxon_tsv(rows: list[dict[str, str]], dest: Path, fieldnames: list[str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def discipline_artifact_basename(discipline_id: int, discipline_name: str | None) -> str:
    base = (discipline_name or "discipline").strip().lower().replace(" ", "_")
    base = _SAFE_SLUG.sub("_", base).strip("_") or "discipline"
    return f"taxon-discipline-{discipline_id}_{base}"


def _union_api_exports(
    client: NorTaxaApiClient,
    root_ids: Iterable[str],
    *,
    include_synonyms: bool = True,
) -> dict[str, dict[str, str]]:
    """Export and union rows keyed by ``scientificNameId`` (``taxonID``)."""
    by_id: dict[str, dict[str, str]] = {}
    for rid in root_ids:
        root = str(rid).strip()
        if not root:
            continue
        for row in client.export_subtree_csv(root, include_synonyms=include_synonyms):
            sid = row_scientific_name_id(row)
            if sid:
                by_id[sid] = row
    return by_id


def _subtract_api_subtrees(
    client: NorTaxaApiClient,
    by_id: dict[str, dict[str, str]],
    subtract_root_ids: Iterable[str],
) -> dict[str, dict[str, str]]:
    remove: set[str] = set()
    for sid in subtract_root_ids:
        root = str(sid).strip()
        if not root:
            continue
        for row in client.export_subtree_csv(root, include_synonyms=True):
            rid = row_scientific_name_id(row)
            if rid:
                remove.add(rid)
    if not remove:
        return by_id
    return {k: v for k, v in by_id.items() if k not in remove}


def _api_label(row: dict[str, str], sid: str) -> dict[str, str]:
    return {
        "taxonID": sid,
        "scientificName": row.get("scientificName") or row.get("presentationName", ""),
        "taxonRank": row.get("taxonRank", ""),
    }


def manifest_roots_for_api_spec(
    by_id: Mapping[str, dict[str, str]],
    spec: NorTaxaSliceSpec,
) -> list[dict]:
    roots = [_api_label(by_id[r], str(r)) if str(r) in by_id else {"taxonID": str(r)} for r in spec.root_taxon_ids]
    if spec.subtract_subtree_taxon_ids:
        return [
            {
                "roots": roots,
                "subtract_subtrees": [
                    _api_label(by_id[s], str(s)) if str(s) in by_id else {"taxonID": str(s)}
                    for s in spec.subtract_subtree_taxon_ids
                ],
            }
        ]
    return roots


def missing_api_root_warnings(by_id: Mapping[str, dict[str, str]], spec: NorTaxaSliceSpec) -> list[str]:
    out: list[str] = []
    for r in spec.root_taxon_ids:
        if str(r).strip() and str(r).strip() not in by_id:
            out.append(f"scientificNameId {r} not returned by NorTaxa API export")
    return out


def run_api_export_for_specify_disciplines(
    *,
    out_dir: Path,
    discipline_jobs: list[
        tuple[int, str | None, int, str | None, NorTaxaSliceSpec]
    ],
    api_base_url: str = NORTAXA_API_BASE_URL,
) -> dict:
    """Export one TSV per discipline via NorTaxa API."""
    client = NorTaxaApiClient(api_base_url)
    artifacts: list[dict] = []
    for did, dname, divid, divname, spec in discipline_jobs:
        by_id = _union_api_exports(client, spec.root_taxon_ids)
        by_id = _subtract_api_subtrees(client, by_id, spec.subtract_subtree_taxon_ids)
        warns = missing_api_root_warnings(by_id, spec)
        subset = list(by_id.values())
        base = discipline_artifact_basename(did, dname)
        fname = f"{base}.tsv"
        out_path = out_dir / fname
        write_taxon_tsv(subset, out_path, NORTAXA_TSV_FIELDNAMES)
        artifacts.append(
            {
                "discipline_id": did,
                "discipline_name": dname,
                "division_id": divid,
                "division_name": divname,
                "artifact": fname,
                "rows": len(subset),
                "roots": manifest_roots_for_api_spec(by_id, spec),
                "root_taxon_ids": list(spec.root_taxon_ids),
                "subtract_subtree_taxon_ids": list(spec.subtract_subtree_taxon_ids),
                "missing_root_warnings": warns,
            }
        )
    manifest = {
        "source": api_base_url,
        "source_type": "nortaxa_api",
        "artifacts": artifacts,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
