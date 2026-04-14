"""Merge NorTaxa DwC ``taxon`` slice TSVs into Specify ``Taxon`` trees (second phase of the flow).

**Provenance (NorTaxa-managed rows)**

- ``source`` = ``"NorTaxa"``
- ``taxonomicserialnumber`` = DwC ``taxonID`` (Artsdatabanken taxon id in the export)
- ``text1`` = last export stamp (UTC ISO date from the flow run)
- ``yesno1`` = ``True`` if the taxon appears in the **current** export slice; ``False`` if it was
  NorTaxa-managed but is **missing** from the current slice (treat as orphaned / authority drift).
  User-created taxa: leave ``source`` unset or not ``NorTaxa`` — they are not touched by orphan logic.

**Behaviour**

- Rows with ``taxonomicStatus`` synonym (and other non-valid statuses) are **not** inserted in
  this version; only ``valid`` (or blank) rows are merged. Synonyms can be added in a follow-up.
- Inserts run in dependency order (parent NorTaxa ids resolved before children). DwC rows with an
  empty ``parentNameUsageID`` attach under the Specify tree **root** (``parent`` is null).
- If a DwC ``taxonRank`` has no matching :class:`~specifyweb.specify.models.Taxontreedefitem`
  ``Name`` for that discipline tree, the row is skipped and counted.
- The same ``TaxonTreeDef`` is only merged **once** per flow run (first discipline wins); a second
  discipline sharing the same tree is skipped with a note in the merge summary.

**Tree bootstrap**

If a discipline has no ``taxontreedef``, or the tree has no rank-0 :class:`Taxontreedefitem`, or no
root :class:`Taxon`, :func:`ensure_discipline_taxon_tree` creates them (mirrors Specify's tree API
pattern: root named **Life**, ``rankid`` 0). Skipped when the flow runs with ``dry_run=True`` and
bootstrap would be required — run with ``dry_run=False`` to create the tree and merge.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NORTAXA_SOURCE = "NorTaxa"


def ensure_discipline_taxon_tree(discipline_id: int, *, dry_run: bool) -> dict[str, Any]:
    """Ensure the discipline has a taxon tree def, rank-0 item, and root ``Taxon`` (``Life``).

    When ``dry_run`` is True, performs no writes. If anything would need to be created, returns
    ``dry_run_blocked: True`` so the caller can skip the merge phase for that discipline.
    """
    from django.db import connection

    from specifyweb.specify.models import Discipline, Taxon, Taxontreedef, Taxontreedefitem

    disc = Discipline.objects.filter(pk=discipline_id).first()
    if disc is None:
        return {"treedef_id": None, "error": "discipline_not_found"}

    out: dict[str, Any] = {
        "treedef_id": None,
        "created_treedef": False,
        "created_rank_item": False,
        "created_root": False,
        "dry_run_blocked": False,
        "message": None,
    }

    td_id: int | None = int(disc.taxontreedef_id) if disc.taxontreedef_id else None

    if td_id is None:
        if dry_run:
            out["dry_run_blocked"] = True
            out["message"] = "dry_run: would create TaxonTreeDef and link to discipline"
            return out
        base = (disc.name or "discipline").strip() or "discipline"
        td_name = f"NorTaxa {base}"[:64]
        td = Taxontreedef.objects.create(name=td_name, discipline_id=disc.id)
        Discipline.objects.filter(pk=disc.id).update(taxontreedef_id=td.id)
        out["created_treedef"] = True
        td_id = int(td.id)
        disc.refresh_from_db()

    assert td_id is not None
    out["treedef_id"] = td_id

    rank0 = Taxontreedefitem.objects.filter(treedef_id=td_id, rankid=0).first()
    if rank0 is None:
        if dry_run:
            out["dry_run_blocked"] = True
            out["message"] = "dry_run: would create TaxonTreeDefItem rankid=0 (Life)"
            return out
        rank0 = Taxontreedefitem.objects.create(
            treedef_id=td_id,
            rankid=0,
            name="Life",
            title="Life",
            parent_id=None,
        )
        out["created_rank_item"] = True

    root = Taxon.objects.filter(definition_id=td_id, parent_id__isnull=True).first()
    if root is None:
        if dry_run:
            out["dry_run_blocked"] = True
            out["message"] = "dry_run: would create root Taxon (Life)"
            return out
        root = Taxon(
            name="Life",
            fullname="Life",
            isaccepted=True,
            rankid=0,
            parent=None,
            definition_id=td_id,
            definitionitem=rank0,
            nodenumber=1,
            highestchildnodenumber=1,
        )
        root.save()
        with connection.cursor() as cur:
            cur.execute("UPDATE taxon SET NodeNumber = 1 WHERE taxonid = %s", [root.id])
        out["created_root"] = True

    return out


def _trunc(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = str(s).strip()
    return s[:n] if len(s) > n else s


def _dwc_status(row: dict[str, str]) -> str:
    return (row.get("taxonomicStatus") or "").strip().lower()


def read_taxon_tsv_rows(tsv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def build_rank_name_to_item(treedef_id: int) -> dict[str, Any]:
    """Map lowercased tree-def rank ``Name`` → :class:`Taxontreedefitem` (first/lowest rankid wins)."""
    from specifyweb.specify.models import Taxontreedefitem

    rank_map: dict[str, Any] = {}
    for item in Taxontreedefitem.objects.filter(treedef_id=treedef_id).order_by("rankid"):
        key = (item.name or "").strip().lower()
        if key and key not in rank_map:
            rank_map[key] = item
    return rank_map


def rank_item_for_dwc_row(row: dict[str, str], rank_map: dict[str, Any]) -> Any | None:
    raw = (row.get("taxonRank") or "").strip().lower()
    if not raw:
        return None
    if raw in rank_map:
        return rank_map[raw]
    # DwC sometimes uses ranks not in a given Specify tree (skip).
    return None


@dataclass
class MergeStats:
    treedef_id: int
    discipline_id: int
    discipline_name: str | None
    dry_run: bool
    orphans_marked: int = 0
    present_refreshed: int = 0
    inserted: int = 0
    skipped_non_valid_status: int = 0
    skipped_unknown_rank: int = 0
    skipped_missing_parent: int = 0
    skipped_no_tree_root: int = 0
    errors: list[str] = field(default_factory=list)


def merge_nortaxa_tsv_into_discipline_tree(
    *,
    discipline_id: int,
    discipline_name: str | None,
    treedef_id: int,
    tsv_path: Path,
    export_stamp: str,
    dry_run: bool,
    logger: Any,
) -> MergeStats:
    """Apply one discipline taxon slice TSV to ``treedef_id``."""
    from django.db import transaction

    from specifyweb.specify.models import Taxon

    stats = MergeStats(
        treedef_id=treedef_id,
        discipline_id=discipline_id,
        discipline_name=discipline_name,
        dry_run=dry_run,
    )
    if not tsv_path.is_file():
        stats.errors.append(f"missing TSV: {tsv_path}")
        return stats

    _, rows = read_taxon_tsv_rows(tsv_path)
    current_ids = {(r.get("taxonID") or "").strip() for r in rows if (r.get("taxonID") or "").strip()}
    if not current_ids:
        logger.info("Merge skip discipline_id=%s: empty taxon slice", discipline_id)
        return stats

    rank_map = build_rank_name_to_item(treedef_id)
    root = Taxon.objects.filter(definition_id=treedef_id, parent_id__isnull=True).first()
    if root is None:
        stats.skipped_no_tree_root = 1
        stats.errors.append(
            f"No root Taxon (parent is null) for TaxonTreeDefID={treedef_id}; "
            "bootstrap the discipline tree before merge."
        )
        return stats

    # --- NorTaxa rows already in Specify for this tree (keyed by export taxon id)
    existing = Taxon.objects.filter(
        definition_id=treedef_id,
        source=NORTAXA_SOURCE,
        taxonomicserialnumber__in=list(current_ids),
    )
    nortaxa_to_taxon: dict[str, Any] = {}
    for t in existing:
        key = (t.taxonomicserialnumber or "").strip()
        if key:
            nortaxa_to_taxon[key] = t

    def _flush_orphans_and_present() -> None:
        orphan_qs = Taxon.objects.filter(
            definition_id=treedef_id,
            source=NORTAXA_SOURCE,
        ).exclude(taxonomicserialnumber__isnull=True).exclude(taxonomicserialnumber__exact="")
        orphan_qs = orphan_qs.exclude(taxonomicserialnumber__in=current_ids)
        present_qs = Taxon.objects.filter(
            definition_id=treedef_id,
            source=NORTAXA_SOURCE,
            taxonomicserialnumber__in=list(current_ids),
        )

        if dry_run:
            stats.orphans_marked = orphan_qs.count()
            stats.present_refreshed = present_qs.count()
            return

        orphans = list(orphan_qs.only("id", "yesno1", "text1"))
        for t in orphans:
            t.yesno1 = False
            t.text1 = export_stamp
        if orphans:
            Taxon.objects.bulk_update(orphans, ["yesno1", "text1"])
            stats.orphans_marked = len(orphans)

        present = list(present_qs.only("id", "yesno1", "text1"))
        for t in present:
            t.yesno1 = True
            t.text1 = export_stamp
        if present:
            Taxon.objects.bulk_update(present, ["yesno1", "text1"])
            stats.present_refreshed = len(present)

    def _update_body_from_dwc(taxon_obj: Any, row: dict[str, str]) -> None:
        name = _trunc(row.get("scientificName"), 256) or taxon_obj.name
        fullname = _trunc(row.get("scientificName"), 512) or name
        author = _trunc(row.get("scientificNameAuthorship"), 128)
        if dry_run:
            return
        changed = False
        if name and taxon_obj.name != name:
            taxon_obj.name = name
            changed = True
        if fullname and taxon_obj.fullname != fullname:
            taxon_obj.fullname = fullname
            changed = True
        if author != (taxon_obj.author or ""):
            taxon_obj.author = author or None
            changed = True
        if changed:
            taxon_obj.save(update_fields=["name", "fullname", "author", "version"])

    # Refresh metadata for taxa still in export; mark NorTaxa-only missing ids as orphaned.
    _flush_orphans_and_present()

    # Update scientific fields for rows we already have.
    for row in rows:
        tid = (row.get("taxonID") or "").strip()
        if not tid or _dwc_status(row) not in ("", "valid"):
            if tid and _dwc_status(row) not in ("", "valid"):
                stats.skipped_non_valid_status += 1
            continue
        t = nortaxa_to_taxon.get(tid)
        if t is not None and hasattr(t, "save"):
            _update_body_from_dwc(t, row)

    # --- Inserts: only valid / blank status, in parent-before-child order
    pending = [
        r
        for r in rows
        if (r.get("taxonID") or "").strip()
        and _dwc_status(r) in ("", "valid")
        and (r.get("taxonID") or "").strip() not in nortaxa_to_taxon
    ]

    max_passes = len(pending) + 3
    for _ in range(max_passes):
        if not pending:
            break
        progressed = 0
        still: list[dict[str, str]] = []
        for row in pending:
            tid = (row.get("taxonID") or "").strip()
            pid = (row.get("parentNameUsageID") or "").strip()
            if pid and pid not in nortaxa_to_taxon:
                still.append(row)
                continue
            parent = root if not pid else nortaxa_to_taxon.get(pid)
            if parent is None:
                still.append(row)
                continue
            di = rank_item_for_dwc_row(row, rank_map)
            if di is None:
                stats.skipped_unknown_rank += 1
                continue
            name = _trunc(row.get("scientificName"), 256)
            if not name:
                stats.skipped_unknown_rank += 1
                continue
            fullname = _trunc(row.get("scientificName"), 512) or name
            author = _trunc(row.get("scientificNameAuthorship"), 128)
            if dry_run:
                stats.inserted += 1
                progressed += 1
                nortaxa_to_taxon[tid] = object()  # placeholder so children resolve in dry-run
                continue

            try:
                with transaction.atomic():
                    obj = Taxon(
                        name=name,
                        fullname=fullname,
                        author=author or None,
                        definition_id=treedef_id,
                        definitionitem=di,
                        parent=parent,
                        rankid=di.rankid,
                        isaccepted=True,
                        source=NORTAXA_SOURCE,
                        taxonomicserialnumber=_trunc(tid, 50),
                        text1=export_stamp,
                        yesno1=True,
                    )
                    obj.save()
            except Exception as exc:  # noqa: BLE001 — surface to manifest
                if len(stats.errors) < 40:
                    stats.errors.append(f"taxonID={tid} name={name!r}: {exc}")
                still.append(row)
                continue
            nortaxa_to_taxon[tid] = obj
            stats.inserted += 1
            progressed += 1
        pending = still
        if progressed == 0:
            break

    if pending:
        stats.skipped_missing_parent = len(pending)
        if len(stats.errors) < 40:
            stats.errors.append(
                f"{len(pending)} row(s) not inserted (missing parent in slice, unknown rank, or DB error)"
            )

    return stats


def merge_stats_to_dict(s: MergeStats) -> dict[str, Any]:
    return {
        "treedef_id": s.treedef_id,
        "discipline_id": s.discipline_id,
        "discipline_name": s.discipline_name,
        "dry_run": s.dry_run,
        "orphans_marked": s.orphans_marked,
        "present_refreshed": s.present_refreshed,
        "inserted": s.inserted,
        "skipped_non_valid_status": s.skipped_non_valid_status,
        "skipped_unknown_rank": s.skipped_unknown_rank,
        "skipped_missing_parent": s.skipped_missing_parent,
        "skipped_no_tree_root": s.skipped_no_tree_root,
        "errors": s.errors,
    }
