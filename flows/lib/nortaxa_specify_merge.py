"""Merge NorTaxa API export TSVs into Specify ``Taxon`` trees.

**Provenance (NorTaxa-managed rows)**

- ``source`` = ``"NorTaxa"``
- ``taxonomicserialnumber`` = NorTaxa ``scientificNameId`` (DwC ``taxonID`` / Oracle ``ADB_TAXON_ID``)
- ``text2`` = NorTaxa ``taxonId`` (taxon concept id for changelog correlation)
- ``text1`` = last sync stamp (UTC ISO date from the flow run)
- ``yesno1`` = ``True`` if the taxon appears in the **current** export slice; ``False`` if orphaned

**Behaviour**

- ``Taxon.name`` uses rank-local epithets from API ``NameString`` (not full binomial).
- ``fullname`` is computed by Specify via ``set_fullnames`` after each merge batch
  (rank ``fullnameseparator`` must be set — default single space, matching Specify).
- Accepted taxa are inserted first; synonym rows are linked via ``acceptedtaxon``.
- Parent changes on re-run update ``Taxon.parent`` when the API parent id drifts.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NORTAXA_RANK_CONFIG_PATH = Path(__file__).with_name("nortaxa_taxon_tree_ranks.json")

# NorTaxa rank labels that differ from Specify treedef item names.
_RANK_ALIASES: dict[str, str] = {
    "form": "forma",
}

from flows.lib.nortaxa_api_client import (
    normalize_taxonomic_status,
    row_accepted_scientific_name_id,
    row_parent_scientific_name_id,
    row_scientific_name_id,
)

NORTAXA_SOURCE = "NorTaxa"
_DEFAULT_FULLNAME_SEPARATOR = " "


def _fullnameseparator_for_level(level: dict[str, Any], *, default: str = _DEFAULT_FULLNAME_SEPARATOR) -> str:
    raw = level.get("fullnameseparator")
    if raw is None:
        raw = level.get("fullNameSeparator")
    if raw is None:
        return default
    return str(raw)


def _rank_levels_by_name(levels: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for lvl in levels:
        key = (lvl.get("name") or "").strip().lower()
        if key:
            out[key] = lvl
    return out


def load_nortaxa_rank_levels() -> list[dict[str, Any]]:
    """Rank template covering NorTaxa export ``Rank`` / ``taxonRank`` values."""
    with _NORTAXA_RANK_CONFIG_PATH.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    default_sep = payload.get("defaultFullNameSeparator", _DEFAULT_FULLNAME_SEPARATOR)
    levels = payload.get("levels") or []
    normalized: list[dict[str, Any]] = []
    for lvl in levels:
        row = dict(lvl)
        if "fullnameseparator" not in row and "fullNameSeparator" not in row:
            row["fullnameseparator"] = default_sep
        normalized.append(row)
    return sorted(normalized, key=lambda x: int(x.get("rank", 0)))


def ensure_taxon_tree_rank_items(treedef_id: int, *, dry_run: bool) -> dict[str, Any]:
    """Ensure ``Taxontreedefitem`` rows exist for all NorTaxa ranks (not just Life)."""
    from specifyweb.specify.models import Taxontreedefitem

    out: dict[str, Any] = {
        "treedef_id": treedef_id,
        "created_rank_items": [],
        "updated_rank_items": [],
        "would_create_rank_items": [],
        "would_update_rank_items": [],
        "dry_run": dry_run,
    }
    levels = load_nortaxa_rank_levels()
    if not levels:
        out["error"] = "empty_rank_config"
        return out

    by_name = _rank_levels_by_name(levels)
    existing_by_name: dict[str, Any] = {}
    for item in Taxontreedefitem.objects.filter(treedef_id=treedef_id):
        key = (item.name or "").strip().lower()
        if key:
            existing_by_name[key] = item

    for key, item in existing_by_name.items():
        lvl = by_name.get(key)
        if lvl is None:
            continue
        desired_sep = _fullnameseparator_for_level(lvl)
        current_sep = item.fullnameseparator
        if current_sep != desired_sep and not (current_sep and str(current_sep).strip()):
            if dry_run:
                out["would_update_rank_items"].append(item.name)
            else:
                item.fullnameseparator = desired_sep
                item.save(update_fields=["fullnameseparator", "version"])
                out["updated_rank_items"].append(item.name)

    missing = [lvl for lvl in levels if (lvl.get("name") or "").strip().lower() not in existing_by_name]
    if missing and dry_run:
        out["would_create_rank_items"] = [str(lvl.get("name") or "") for lvl in missing]

    if missing and not dry_run:
        for lvl in missing:
            name = str(lvl.get("name") or "").strip()
            if not name:
                continue
            item = Taxontreedefitem.objects.create(
                treedef_id=treedef_id,
                rankid=int(lvl.get("rank", 0)),
                name=name,
                title=name,
                isenforced=bool(lvl.get("enforced", True)),
                isinfullname=bool(lvl.get("infullname", False)),
                fullnameseparator=_fullnameseparator_for_level(lvl),
                parent_id=None,
            )
            existing_by_name[name.lower()] = item
            out["created_rank_items"].append(name)

    if not dry_run:
        _rechain_taxon_tree_rank_parents(treedef_id)

    if out["created_rank_items"]:
        logger.info(
            "Created %s taxon rank items on TaxonTreeDefID=%s: %s",
            len(out["created_rank_items"]),
            treedef_id,
            ", ".join(out["created_rank_items"][:8])
            + ("..." if len(out["created_rank_items"]) > 8 else ""),
        )
    if out["updated_rank_items"]:
        logger.info(
            "Set fullnameseparator on %s rank item(s) for TaxonTreeDefID=%s",
            len(out["updated_rank_items"]),
            treedef_id,
        )
    return out


def ensure_taxon_treedef_defaults(treedef_id: int) -> None:
    """Align ``TaxonTreeDef`` metadata with Specify default tree behaviour."""
    from specifyweb.specify.models import Taxontreedef

    Taxontreedef.objects.filter(pk=treedef_id, fullnamedirection__isnull=True).update(
        fullnamedirection=1
    )


def _rechain_taxon_tree_rank_parents(treedef_id: int) -> None:
    from specifyweb.specify.models import Taxontreedefitem

    items = list(Taxontreedefitem.objects.filter(treedef_id=treedef_id).order_by("rankid", "id"))
    parent = None
    changed: list[Any] = []
    for item in items:
        desired_parent_id = parent.id if parent is not None else None
        if item.parent_id != desired_parent_id:
            item.parent_id = desired_parent_id
            changed.append(item)
        parent = item
    if changed:
        Taxontreedefitem.objects.bulk_update(changed, ["parent_id"])


def ensure_discipline_taxon_tree(discipline_id: int, *, dry_run: bool) -> dict[str, Any]:
    """Ensure the discipline has a taxon tree def, rank-0 item, and root ``Taxon`` (``Life``)."""
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
        td = Taxontreedef.objects.create(
            name=td_name,
            discipline_id=disc.id,
            fullnamedirection=1,
        )
        Discipline.objects.filter(pk=disc.id).update(taxontreedef_id=td.id)
        out["created_treedef"] = True
        td_id = int(td.id)
        disc.refresh_from_db()

    assert td_id is not None
    out["treedef_id"] = td_id
    if not dry_run:
        ensure_taxon_treedef_defaults(td_id)

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
            fullnameseparator=_DEFAULT_FULLNAME_SEPARATOR,
            parent_id=None,
        )
        out["created_rank_item"] = True
    elif not dry_run and not (rank0.fullnameseparator or "").strip():
        rank0.fullnameseparator = _DEFAULT_FULLNAME_SEPARATOR
        rank0.save(update_fields=["fullnameseparator", "version"])

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

    rank_items = ensure_taxon_tree_rank_items(td_id, dry_run=dry_run)
    out["rank_items"] = rank_items
    if rank_items.get("would_create_rank_items"):
        out["dry_run_blocked"] = True
        out["message"] = (
            "dry_run: would create taxon rank items: "
            + ", ".join(rank_items["would_create_rank_items"][:12])
        )
    return out


def _trunc(s: str | None, n: int) -> str:
    if not s:
        return ""
    s = str(s).strip()
    return s[:n] if len(s) > n else s


def _row_status(row: dict[str, str]) -> str:
    return normalize_taxonomic_status(row.get("taxonomicStatus") or "")


def _row_name(row: dict[str, str]) -> str:
    return _trunc(row.get("scientificName") or row.get("NameString"), 256)


def _row_author(row: dict[str, str]) -> str:
    return _trunc(row.get("scientificNameAuthorship") or row.get("Author"), 128)


def _row_taxon_id(row: dict[str, str]) -> str:
    return _trunc(row.get("taxonId"), 32)


def _row_vernacular(row: dict[str, str]) -> str:
    return _trunc(row.get("vernacularNameBokmaal"), 128)


def read_taxon_tsv_rows(tsv_path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with tsv_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def build_rank_name_to_item(treedef_id: int) -> dict[str, Any]:
    from specifyweb.specify.models import Taxontreedefitem

    rank_map: dict[str, Any] = {}
    for item in Taxontreedefitem.objects.filter(treedef_id=treedef_id).order_by("rankid"):
        key = (item.name or "").strip().lower()
        if key and key not in rank_map:
            rank_map[key] = item
    return rank_map


def rank_item_for_row(row: dict[str, str], rank_map: dict[str, Any]) -> Any | None:
    raw = (row.get("taxonRank") or row.get("Rank") or "").strip().lower()
    if not raw:
        return None
    raw = _RANK_ALIASES.get(raw, raw)
    return rank_map.get(raw)


def _try_adopt_existing_taxon(
    *,
    treedef_id: int,
    parent: Any,
    name: str,
    rankid: int,
    tid: str,
    taxon_id: str,
    export_stamp: str,
    dry_run: bool,
) -> tuple[Any | None, bool]:
    from django.db.models import Q

    from specifyweb.specify.models import Taxon

    serial = _trunc(tid, 50)
    blank_serial = Q(taxonomicserialnumber__isnull=True) | Q(taxonomicserialnumber__exact="")
    cand = (
        Taxon.objects.filter(
            definition_id=treedef_id,
            parent_id=parent.id,
            name=name,
            rankid=rankid,
        )
        .filter(blank_serial)
        .first()
    )
    if cand is None:
        return None, False
    if dry_run:
        return cand, True
    from django.db import transaction

    with transaction.atomic():
        cand.source = NORTAXA_SOURCE
        cand.taxonomicserialnumber = serial
        cand.text1 = export_stamp
        cand.text2 = taxon_id or cand.text2
        cand.yesno1 = True
        cand.save(update_fields=["source", "taxonomicserialnumber", "text1", "text2", "yesno1", "version"])
    return cand, True


def _link_synonym_to_accepted(synonym: Any, accepted: Any, *, dry_run: bool) -> bool:
    """Point ``synonym`` at ``accepted`` (Specify synonymy semantics)."""
    if synonym.id == accepted.id:
        return False
    if dry_run:
        return True
    from django.db import transaction

    from specifyweb.specify.models import Determination

    # Specify ``Taxon`` uses ``Tree.save()`` which ``select_for_update()``s the row.
    with transaction.atomic():
        synonym.acceptedtaxon_id = accepted.id
        synonym.isaccepted = False
        synonym.save(update_fields=["acceptedtaxon", "isaccepted", "version"])
        Determination.objects.filter(taxon=synonym).update(preferredtaxon=accepted)
        Determination.objects.filter(preferredtaxon=synonym).update(preferredtaxon=accepted)
    return True


def _rebuild_fullnames(treedef_id: int, *, dry_run: bool) -> bool:
    if dry_run:
        return False
    from specifyweb.backend.trees.extras import set_fullnames
    from specifyweb.specify.models import Taxontreedef

    td = Taxontreedef.objects.filter(pk=treedef_id).first()
    if td is None:
        return False
    set_fullnames(td, null_only=False, node_number_range=None)
    return True


@dataclass
class MergeStats:
    treedef_id: int
    discipline_id: int
    discipline_name: str | None
    dry_run: bool
    orphans_marked: int = 0
    present_refreshed: int = 0
    inserted: int = 0
    synonyms_inserted: int = 0
    synonyms_linked: int = 0
    parents_updated: int = 0
    skipped_non_valid_status: int = 0
    skipped_unknown_rank: int = 0
    skipped_missing_parent: int = 0
    skipped_no_tree_root: int = 0
    skipped_missing_accepted: int = 0
    reparented_out_of_slice_to_root: int = 0
    adopted_pre_existing: int = 0
    fullnames_rebuilt: bool = False
    errors: list[str] = field(default_factory=list)


def _resolve_parent(
    *,
    row: dict[str, str],
    root: Any,
    nortaxa_to_taxon: dict[str, Any],
    current_ids: set[str],
) -> tuple[Any | None, bool]:
    pid = row_parent_scientific_name_id(row)
    if not pid:
        return root, False
    parent = nortaxa_to_taxon.get(pid)
    if parent is not None:
        return parent, False
    if pid not in current_ids:
        return root, True
    return None, False


def _insert_taxon_row(
    *,
    row: dict[str, str],
    treedef_id: int,
    tid: str,
    parent: Any,
    di: Any,
    export_stamp: str,
    dry_run: bool,
    is_accepted: bool,
) -> Any | None:
    from django.db import transaction

    from specifyweb.specify.models import Taxon

    name = _row_name(row)
    if not name:
        return None
    author = _row_author(row)
    taxon_id = _row_taxon_id(row)
    common = _row_vernacular(row)

    if dry_run:
        return object()

    with transaction.atomic():
        obj = Taxon(
            name=name,
            fullname=None,
            author=author or None,
            commonname=common or None,
            definition_id=treedef_id,
            definitionitem=di,
            parent=parent,
            rankid=di.rankid,
            isaccepted=is_accepted,
            source=NORTAXA_SOURCE,
            taxonomicserialnumber=_trunc(tid, 50),
            text1=export_stamp,
            text2=taxon_id or None,
            yesno1=True,
        )
        obj.save()
    return obj


def _update_existing_taxon(
    taxon_obj: Any,
    row: dict[str, str],
    *,
    nortaxa_to_taxon: dict[str, Any],
    root: Any,
    current_ids: set[str],
    export_stamp: str,
    dry_run: bool,
    stats: MergeStats,
) -> None:
    name = _row_name(row) or taxon_obj.name
    author = _row_author(row)
    taxon_id = _row_taxon_id(row)
    common = _row_vernacular(row)
    parent, reparented = _resolve_parent(
        row=row,
        root=root,
        nortaxa_to_taxon=nortaxa_to_taxon,
        current_ids=current_ids,
    )
    if reparented:
        stats.reparented_out_of_slice_to_root += 1

    if dry_run:
        return

    from django.db import transaction

    changed = False
    if name and taxon_obj.name != name:
        taxon_obj.name = name
        changed = True
    if author != (taxon_obj.author or ""):
        taxon_obj.author = author or None
        changed = True
    if common and (taxon_obj.commonname or "") != common:
        taxon_obj.commonname = common
        changed = True
    if taxon_id and (taxon_obj.text2 or "") != taxon_id:
        taxon_obj.text2 = taxon_id
        changed = True
    if taxon_obj.text1 != export_stamp:
        taxon_obj.text1 = export_stamp
        changed = True
    if parent is not None and taxon_obj.parent_id != parent.id:
        taxon_obj.parent_id = parent.id
        changed = True
        stats.parents_updated += 1
    if changed:
        with transaction.atomic():
            taxon_obj.save()


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
    current_ids = {row_scientific_name_id(r) for r in rows if row_scientific_name_id(r)}
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

    existing = (
        Taxon.objects.filter(definition_id=treedef_id, taxonomicserialnumber__in=list(current_ids))
        .exclude(taxonomicserialnumber__isnull=True)
        .exclude(taxonomicserialnumber__exact="")
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
            taxonomicserialnumber__in=list(current_ids),
        ).exclude(taxonomicserialnumber__isnull=True).exclude(taxonomicserialnumber__exact="")

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

    _flush_orphans_and_present()

    accepted_rows = [r for r in rows if _row_status(r) == "accepted"]
    synonym_rows = [r for r in rows if _row_status(r) == "synonym"]

    for row in accepted_rows:
        tid = row_scientific_name_id(row)
        if not tid:
            continue
        t = nortaxa_to_taxon.get(tid)
        if t is not None and hasattr(t, "save"):
            _update_existing_taxon(
                t,
                row,
                nortaxa_to_taxon=nortaxa_to_taxon,
                root=root,
                current_ids=current_ids,
                export_stamp=export_stamp,
                dry_run=dry_run,
                stats=stats,
            )

    pending = [
        r
        for r in accepted_rows
        if row_scientific_name_id(r) and row_scientific_name_id(r) not in nortaxa_to_taxon
    ]
    max_passes = len(pending) + 3
    for _ in range(max_passes):
        if not pending:
            break
        progressed = 0
        still: list[dict[str, str]] = []
        for row in pending:
            tid = row_scientific_name_id(row)
            parent, reparented = _resolve_parent(
                row=row,
                root=root,
                nortaxa_to_taxon=nortaxa_to_taxon,
                current_ids=current_ids,
            )
            if parent is None:
                still.append(row)
                continue
            di = rank_item_for_row(row, rank_map)
            if di is None:
                stats.skipped_unknown_rank += 1
                continue
            if reparented:
                stats.reparented_out_of_slice_to_root += 1

            adopted_obj, adopted = _try_adopt_existing_taxon(
                treedef_id=treedef_id,
                parent=parent,
                name=_row_name(row),
                rankid=di.rankid,
                tid=tid,
                taxon_id=_row_taxon_id(row),
                export_stamp=export_stamp,
                dry_run=dry_run,
            )
            if adopted:
                stats.adopted_pre_existing += 1
                nortaxa_to_taxon[tid] = adopted_obj
                progressed += 1
                continue

            try:
                obj = _insert_taxon_row(
                    row=row,
                    treedef_id=treedef_id,
                    tid=tid,
                    parent=parent,
                    di=di,
                    export_stamp=export_stamp,
                    dry_run=dry_run,
                    is_accepted=True,
                )
            except Exception as exc:  # noqa: BLE001
                if len(stats.errors) < 40:
                    stats.errors.append(f"taxonID={tid} name={_row_name(row)!r}: {exc}")
                still.append(row)
                continue
            if obj is None:
                stats.skipped_unknown_rank += 1
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
            stats.errors.append(f"{len(pending)} accepted row(s) not inserted (missing parent or error)")

    # --- Synonyms: insert if missing, then link to accepted
    syn_pending = [
        r
        for r in synonym_rows
        if row_scientific_name_id(r) and row_scientific_name_id(r) not in nortaxa_to_taxon
    ]
    for _ in range(len(syn_pending) + 3):
        if not syn_pending:
            break
        progressed = 0
        still_syn: list[dict[str, str]] = []
        for row in syn_pending:
            tid = row_scientific_name_id(row)
            aid = row_accepted_scientific_name_id(row) or tid
            accepted = nortaxa_to_taxon.get(aid)
            if accepted is None:
                still_syn.append(row)
                continue
            parent, reparented = _resolve_parent(
                row=row,
                root=root,
                nortaxa_to_taxon=nortaxa_to_taxon,
                current_ids=current_ids,
            )
            if parent is None:
                still_syn.append(row)
                continue
            di = rank_item_for_row(row, rank_map)
            if di is None:
                stats.skipped_unknown_rank += 1
                continue
            if reparented:
                stats.reparented_out_of_slice_to_root += 1
            try:
                obj = _insert_taxon_row(
                    row=row,
                    treedef_id=treedef_id,
                    tid=tid,
                    parent=parent,
                    di=di,
                    export_stamp=export_stamp,
                    dry_run=dry_run,
                    is_accepted=False,
                )
            except Exception as exc:  # noqa: BLE001
                if len(stats.errors) < 40:
                    stats.errors.append(f"synonym taxonID={tid}: {exc}")
                still_syn.append(row)
                continue
            if obj is None:
                still_syn.append(row)
                continue
            nortaxa_to_taxon[tid] = obj
            stats.synonyms_inserted += 1
            progressed += 1
        syn_pending = still_syn
        if progressed == 0:
            break

    for row in synonym_rows:
        tid = row_scientific_name_id(row)
        aid = row_accepted_scientific_name_id(row)
        if not tid or not aid:
            stats.skipped_missing_accepted += 1
            continue
        synonym = nortaxa_to_taxon.get(tid)
        accepted = nortaxa_to_taxon.get(aid)
        if synonym is None or accepted is None:
            stats.skipped_missing_accepted += 1
            continue
        if not hasattr(synonym, "save"):
            continue
        if synonym.acceptedtaxon_id == getattr(accepted, "id", None) and not synonym.isaccepted:
            continue
        if _link_synonym_to_accepted(synonym, accepted, dry_run=dry_run):
            stats.synonyms_linked += 1

    stats.skipped_non_valid_status = len(
        [r for r in rows if _row_status(r) not in ("accepted", "synonym")]
    )
    stats.fullnames_rebuilt = _rebuild_fullnames(treedef_id, dry_run=dry_run)
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
        "synonyms_inserted": s.synonyms_inserted,
        "synonyms_linked": s.synonyms_linked,
        "parents_updated": s.parents_updated,
        "skipped_non_valid_status": s.skipped_non_valid_status,
        "skipped_unknown_rank": s.skipped_unknown_rank,
        "skipped_missing_parent": s.skipped_missing_parent,
        "skipped_missing_accepted": s.skipped_missing_accepted,
        "skipped_no_tree_root": s.skipped_no_tree_root,
        "reparented_out_of_slice_to_root": s.reparented_out_of_slice_to_root,
        "adopted_pre_existing": s.adopted_pre_existing,
        "fullnames_rebuilt": s.fullnames_rebuilt,
        "errors": s.errors,
    }
