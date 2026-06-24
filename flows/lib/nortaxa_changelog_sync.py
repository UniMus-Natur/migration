"""Incremental NorTaxa taxonomy sync via ``TaxonName/ChangeLog``.

Auto-applies: ``TaxonCreated``, ``Inserted``, ``ParentChange``, ``AuthorChanged``,
``ValidNameChange``, ``Swap``.

Queues for curator review: ``Merge``, ``Split``, ``Delete``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flows.lib.nortaxa_api_client import NorTaxaApiClient, normalize_taxonomic_status
from flows.lib.nortaxa_specify_merge import (
    NORTAXA_SOURCE,
    _link_synonym_to_accepted,
    _rebuild_fullnames,
    _row_author,
    _row_name,
    build_rank_name_to_item,
    rank_item_for_row,
)

REVIEW_CHANGE_TYPES = frozenset({"Merge", "Split", "Delete"})
AUTO_CHANGE_TYPES = frozenset(
    {
        "TaxonCreated",
        "Inserted",
        "ParentChange",
        "AuthorChanged",
        "ValidNameChange",
        "Swap",
    }
)

DEFAULT_WATERMARK_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "nortaxa-changelog-state.json"


@dataclass
class ChangelogSyncStats:
    events_seen: int = 0
    events_in_scope: int = 0
    auto_applied: int = 0
    review_queued: int = 0
    skipped_unknown: int = 0
    errors: list[str] = field(default_factory=list)
    review_items: list[dict[str, Any]] = field(default_factory=list)
    last_cursor: str | None = None


def load_changelog_watermark(path: Path = DEFAULT_WATERMARK_PATH) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_changelog_watermark(
    state: dict[str, Any],
    path: Path = DEFAULT_WATERMARK_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _changelog_entries(event: dict[str, Any]) -> list[dict[str, Any]]:
    return list(event.get("dataAfter") or event.get("dataBefore") or [])


def _entry_ids(entry: dict[str, Any]) -> tuple[str, str]:
    name_id = str(entry.get("nameId") or entry.get("id") or "").strip()
    taxon_id = str(entry.get("taxonId") or "").strip()
    return name_id, taxon_id


def _event_in_scope(
    event: dict[str, Any],
    *,
    serial_ids: set[str],
    concept_ids: set[str],
) -> bool:
    for entry in _changelog_entries(event):
        name_id, taxon_id = _entry_ids(entry)
        if name_id in serial_ids or taxon_id in concept_ids:
            return True
    return False


def _presentation(entry: dict[str, Any]) -> str:
    sci = entry.get("scientificName") or {}
    if isinstance(sci, dict):
        return (
            sci.get("scientificNamePresentation")
            or sci.get("scientificName")
            or ""
        )
    return ""


def _rank(entry: dict[str, Any]) -> str:
    sci = entry.get("scientificName") or {}
    if isinstance(sci, dict):
        return (sci.get("taxonRank") or "").strip()
    return ""


def _author(entry: dict[str, Any]) -> str:
    sci = entry.get("scientificName") or {}
    if isinstance(sci, dict):
        return (sci.get("scientificNameAuthorship") or "").strip()
    return ""


def _status(entry: dict[str, Any]) -> str:
    sci = entry.get("scientificName") or {}
    if isinstance(sci, dict):
        return normalize_taxonomic_status(sci.get("taxonomicStatus") or "")
    return "accepted"


def _name_string(entry: dict[str, Any]) -> str:
    sci = entry.get("scientificName") or {}
    if isinstance(sci, dict):
        return (sci.get("scientificName") or "").strip()
    return ""


def _parent_name_id(entry: dict[str, Any]) -> str:
    pid = entry.get("parentTaxonNameId")
    if pid is not None:
        return str(pid).strip()
    parent = entry.get("parentScientificName") or {}
    if isinstance(parent, dict):
        return str(parent.get("nameId") or "").strip()
    return ""


def _count_determinations_for_serials(serials: set[str]) -> int:
    if not serials:
        return 0
    from specifyweb.specify.models import Determination, Taxon

    taxon_ids = list(
        Taxon.objects.filter(taxonomicserialnumber__in=list(serials)).values_list("id", flat=True)
    )
    if not taxon_ids:
        return 0
    return Determination.objects.filter(taxon_id__in=taxon_ids).count()


def _taxon_by_serial(treedef_id: int, serial: str) -> Any | None:
    from specifyweb.specify.models import Taxon

    if not serial:
        return None
    return (
        Taxon.objects.filter(definition_id=treedef_id, taxonomicserialnumber=serial)
        .exclude(taxonomicserialnumber__isnull=True)
        .first()
    )


def _apply_parent_change(
    entry: dict[str, Any],
    *,
    treedef_id: int,
    taxon_map: dict[str, Any],
    root: Any,
    dry_run: bool,
) -> bool:
    name_id, taxon_id = _entry_ids(entry)
    taxon = taxon_map.get(name_id) or _taxon_by_serial(treedef_id, name_id)
    if taxon is None:
        return False
    pid = _parent_name_id(entry)
    parent = taxon_map.get(pid) or _taxon_by_serial(treedef_id, pid)
    if parent is None:
        parent = root
    if dry_run:
        return True
    if taxon.parent_id != parent.id:
        taxon.parent_id = parent.id
        taxon.text2 = taxon_id[:32] if taxon_id else taxon.text2
        taxon.save(update_fields=["parent", "text2", "version"])
        return True
    return False


def _apply_author_change(
    entry: dict[str, Any],
    *,
    treedef_id: int,
    taxon_map: dict[str, Any],
    dry_run: bool,
) -> bool:
    name_id, _ = _entry_ids(entry)
    taxon = taxon_map.get(name_id) or _taxon_by_serial(treedef_id, name_id)
    if taxon is None:
        return False
    author = _author(entry)[:128]
    if dry_run:
        return True
    if (taxon.author or "") != author:
        taxon.author = author or None
        taxon.save(update_fields=["author", "version"])
        return True
    return False


def _apply_insert_from_entry(
    entry: dict[str, Any],
    *,
    treedef_id: int,
    taxon_map: dict[str, Any],
    rank_map: dict[str, Any],
    root: Any,
    export_stamp: str,
    dry_run: bool,
) -> bool:
    from specifyweb.specify.models import Taxon

    name_id, taxon_id = _entry_ids(entry)
    if not name_id or name_id in taxon_map:
        return False
    row = {
        "scientificName": _name_string(entry),
        "taxonRank": _rank(entry),
        "taxonomicStatus": _status(entry),
        "scientificNameAuthorship": _author(entry),
        "taxonId": taxon_id,
        "parentNameUsageID": _parent_name_id(entry),
    }
    di = rank_item_for_row(row, rank_map)
    if di is None:
        return False
    pid = _parent_name_id(entry)
    parent = taxon_map.get(pid) or _taxon_by_serial(treedef_id, pid) or root
    name = _row_name(row)
    if not name:
        return False
    is_accepted = _status(entry) == "accepted"
    if dry_run:
        taxon_map[name_id] = object()
        return True
    obj = Taxon(
        name=name,
        fullname=None,
        author=_row_author(row) or None,
        definition_id=treedef_id,
        definitionitem=di,
        parent=parent,
        rankid=di.rankid,
        isaccepted=is_accepted,
        source=NORTAXA_SOURCE,
        taxonomicserialnumber=name_id[:50],
        text1=export_stamp,
        text2=taxon_id[:32] if taxon_id else None,
        yesno1=True,
    )
    obj.save()
    taxon_map[name_id] = obj
    return True


def _apply_valid_name_or_swap(
    event: dict[str, Any],
    *,
    treedef_id: int,
    taxon_map: dict[str, Any],
    dry_run: bool,
) -> int:
    """Synonymize names that are no longer accepted toward the new accepted set."""
    linked = 0
    after_by_taxon: dict[str, list[dict[str, Any]]] = {}
    for entry in event.get("dataAfter") or []:
        _, tid = _entry_ids(entry)
        if tid:
            after_by_taxon.setdefault(tid, []).append(entry)

    for entry in event.get("dataBefore") or []:
        name_id, taxon_id = _entry_ids(entry)
        if _status(entry) != "accepted":
            continue
        after_entries = after_by_taxon.get(taxon_id, [])
        new_accepted = next((e for e in after_entries if _status(e) == "accepted"), None)
        if new_accepted is None:
            continue
        new_id, _ = _entry_ids(new_accepted)
        if not new_id or new_id == name_id:
            continue
        old_taxon = taxon_map.get(name_id) or _taxon_by_serial(treedef_id, name_id)
        new_taxon = taxon_map.get(new_id) or _taxon_by_serial(treedef_id, new_id)
        if old_taxon is None or new_taxon is None:
            continue
        if _link_synonym_to_accepted(old_taxon, new_taxon, dry_run=dry_run):
            linked += 1
    return linked


def _build_review_item(event: dict[str, Any], *, serial_ids: set[str]) -> dict[str, Any]:
    entries = _changelog_entries(event)
    serials: set[str] = set()
    taxon_ids: set[str] = set()
    presentations: list[str] = []
    for entry in entries:
        name_id, taxon_id = _entry_ids(entry)
        if name_id:
            serials.add(name_id)
        if taxon_id:
            taxon_ids.add(taxon_id)
        pres = _presentation(entry)
        if pres:
            presentations.append(pres)
    affected_serials = serials & serial_ids
    return {
        "changeType": event.get("changeType"),
        "changeDate": event.get("changeDate"),
        "changeByUser": event.get("changeByUser"),
        "changeRemarks": event.get("changeRemarks"),
        "taxonIds": sorted(taxon_ids),
        "scientificNameIds": sorted(serials),
        "presentations": presentations,
        "determination_count": _count_determinations_for_serials(affected_serials),
        "dataBefore": event.get("dataBefore"),
        "dataAfter": event.get("dataAfter"),
    }


def _load_scope_ids(treedef_ids: list[int]) -> tuple[set[str], set[str], dict[str, Any]]:
    from specifyweb.specify.models import Taxon

    serial_ids: set[str] = set()
    concept_ids: set[str] = set()
    taxon_map: dict[str, Any] = {}
    qs = Taxon.objects.filter(definition_id__in=treedef_ids).exclude(
        taxonomicserialnumber__isnull=True
    ).exclude(taxonomicserialnumber__exact="")
    for t in qs:
        serial = (t.taxonomicserialnumber or "").strip()
        if serial:
            serial_ids.add(serial)
            taxon_map[serial] = t
        concept = (t.text2 or "").strip()
        if concept:
            concept_ids.add(concept)
    return serial_ids, concept_ids, taxon_map


def sync_nortaxa_changelog(
    *,
    treedef_ids: list[int],
    from_date: str | None = None,
    watermark_path: Path = DEFAULT_WATERMARK_PATH,
    dry_run: bool = True,
    api_base_url: str = "https://nortaxa.artsdatabanken.no",
    logger: Any | None = None,
) -> dict[str, Any]:
    """Poll NorTaxa changelog and apply auto rules for the given tree defs."""
    from specifyweb.specify.models import Taxon

    stats = ChangelogSyncStats()
    if not treedef_ids:
        return _changelog_stats_to_dict(stats)

    state = load_changelog_watermark(watermark_path)
    since = from_date or state.get("last_changelog_cursor") or state.get("last_sync_at")
    if not since:
        since = "2020-01-01"

    serial_ids, concept_ids, taxon_map = _load_scope_ids(treedef_ids)
    client = NorTaxaApiClient(api_base_url)
    export_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    rank_maps: dict[int, dict[str, Any]] = {td: build_rank_name_to_item(td) for td in treedef_ids}
    roots: dict[int, Any] = {}
    for td in treedef_ids:
        roots[td] = Taxon.objects.filter(definition_id=td, parent_id__isnull=True).first()

    last_cursor = since
    for event in client.iter_changelog(since, limit=500):
        stats.events_seen += 1
        change_type = (event.get("changeType") or "").strip()
        if not change_type:
            stats.skipped_unknown += 1
            continue
        if not _event_in_scope(event, serial_ids=serial_ids, concept_ids=concept_ids):
            # New taxa outside current Specify scope are still auto-inserted when created.
            if change_type not in ("TaxonCreated", "Inserted"):
                continue
        stats.events_in_scope += 1

        if change_type in REVIEW_CHANGE_TYPES:
            item = _build_review_item(event, serial_ids=serial_ids)
            stats.review_items.append(item)
            stats.review_queued += 1
            continue

        if change_type not in AUTO_CHANGE_TYPES:
            stats.skipped_unknown += 1
            continue

        applied_any = False
        for td in treedef_ids:
            root = roots.get(td)
            if root is None:
                continue
            rank_map = rank_maps[td]
            if change_type in ("TaxonCreated", "Inserted"):
                for entry in event.get("dataAfter") or []:
                    if _apply_insert_from_entry(
                        entry,
                        treedef_id=td,
                        taxon_map=taxon_map,
                        rank_map=rank_map,
                        root=root,
                        export_stamp=export_stamp,
                        dry_run=dry_run,
                    ):
                        applied_any = True
                        name_id, _ = _entry_ids(entry)
                        if name_id:
                            serial_ids.add(name_id)
            elif change_type == "ParentChange":
                for entry in event.get("dataAfter") or []:
                    if _apply_parent_change(
                        entry,
                        treedef_id=td,
                        taxon_map=taxon_map,
                        root=root,
                        dry_run=dry_run,
                    ):
                        applied_any = True
            elif change_type == "AuthorChanged":
                for entry in event.get("dataAfter") or []:
                    if _apply_author_change(
                        entry,
                        treedef_id=td,
                        taxon_map=taxon_map,
                        dry_run=dry_run,
                    ):
                        applied_any = True
            elif change_type in ("ValidNameChange", "Swap"):
                linked = _apply_valid_name_or_swap(
                    event,
                    treedef_id=td,
                    taxon_map=taxon_map,
                    dry_run=dry_run,
                )
                if linked:
                    applied_any = True

        if applied_any:
            stats.auto_applied += 1
        if event.get("changeDate"):
            last_cursor = str(event["changeDate"])

    stats.last_cursor = last_cursor
    if not dry_run and last_cursor:
        save_changelog_watermark(
            {
                "last_changelog_cursor": last_cursor,
                "last_sync_at": export_stamp,
            },
            watermark_path,
        )

    for td in treedef_ids:
        _rebuild_fullnames(td, dry_run=dry_run)

    result = _changelog_stats_to_dict(stats)
    if logger is not None:
        logger.info(
            "Changelog sync: seen=%s in_scope=%s auto=%s review=%s dry_run=%s",
            stats.events_seen,
            stats.events_in_scope,
            stats.auto_applied,
            stats.review_queued,
            dry_run,
        )
    return result


def _changelog_stats_to_dict(stats: ChangelogSyncStats) -> dict[str, Any]:
    return {
        "events_seen": stats.events_seen,
        "events_in_scope": stats.events_in_scope,
        "auto_applied": stats.auto_applied,
        "review_queued": stats.review_queued,
        "skipped_unknown": stats.skipped_unknown,
        "last_cursor": stats.last_cursor,
        "errors": stats.errors,
        "review_items": stats.review_items,
    }
