"""Detect MUSIT hybrid classterms and archive parent species for later cleanup.

MUSIT stores hybrids as a formula string on ``CLASSIFICATION_TERM`` plus N parent
links in ``CLASSTERM_LATIN_NAME`` (not the empty ``HYBRID_*`` tables). Specify's
native hybrid model only allows two parents on ``Taxon``, so migration archives
all parents in ``CollectionObject.text3`` and flags the determination.
"""

from __future__ import annotations

import re
from typing import Any

from flows.lib.musit_determiner_actors import determination_dedupe_key

# Multiplication / hybrid markers seen in botany classterms (ASCII x and ×).
# Require whitespace around ASCII x/X so "Salix" is not split on its "x".
_HYBRID_MARKER_RE = re.compile(r"(?:×|\s[xX]\s)")
_HYBRID_SPLIT_RE = re.compile(r"\s*[×]\s*|\s+[xX]\s+")


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def classterm_looks_hybrid(*names: Any) -> bool:
    """True when any name looks like a hybrid formula (contains × / `` x ``)."""
    for raw in names:
        text = _clean(raw)
        if text and _HYBRID_MARKER_RE.search(text):
            return True
    return False


def _parent_payload(row: dict[str, Any]) -> dict[str, Any] | None:
    latin_name_id = row.get("latin_name_id")
    if latin_name_id is None and not _clean(row.get("latin_name")) and not _clean(
        row.get("full_name")
    ):
        return None
    payload: dict[str, Any] = {
        "latin_name_id": int(latin_name_id) if latin_name_id is not None else None,
        "latin_name": _clean(row.get("latin_name")),
        "full_name": _clean(row.get("full_name")),
        "full_name_author": _clean(row.get("full_name_author")),
        "adb_latin_name_id": (
            int(row["adb_latin_name_id"])
            if row.get("adb_latin_name_id") is not None
            else None
        ),
        "adb_taxon_id": (
            int(row["adb_taxon_id"]) if row.get("adb_taxon_id") is not None else None
        ),
        "nhm_taxon_id": (
            int(row["nhm_taxon_id"]) if row.get("nhm_taxon_id") is not None else None
        ),
        "precision_code": _clean(row.get("precision_code")),
        "relation_type": (
            int(row["relation_type"]) if row.get("relation_type") is not None else None
        ),
    }
    return payload


def _parent_dedupe_key(parent: dict[str, Any]) -> tuple[Any, ...]:
    if parent.get("latin_name_id") is not None:
        return ("ln", parent["latin_name_id"])
    return (
        "name",
        parent.get("full_name") or parent.get("latin_name"),
        parent.get("adb_latin_name_id"),
        parent.get("adb_taxon_id"),
    )


def _order_parents_by_formula(
    parents: list[dict[str, Any]], formula: str | None
) -> list[dict[str, Any]]:
    """Best-effort order parents to match left-to-right formula segments."""
    if not formula or len(parents) < 2:
        return parents
    segments = [s.strip() for s in _HYBRID_SPLIT_RE.split(formula) if s and s.strip()]
    if len(segments) < 2:
        return parents

    remaining = list(parents)
    ordered: list[dict[str, Any]] = []
    for segment in segments:
        seg_l = segment.lower()
        match_idx = None
        for i, parent in enumerate(remaining):
            epithet = (_clean(parent.get("latin_name")) or "").lower()
            full = (_clean(parent.get("full_name")) or "").lower()
            if epithet and epithet in seg_l:
                match_idx = i
                break
            if full and (full in seg_l or seg_l in full):
                match_idx = i
                break
        if match_idx is None:
            continue
        ordered.append(remaining.pop(match_idx))
    ordered.extend(remaining)
    return ordered


def hybrid_parents_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collect unique parent species from classterm_latin_name fan-out rows."""
    parents: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    # Prefer first-seen adb_taxon_id; classification_taxon join can fan out.
    for row in rows:
        parent = _parent_payload(row)
        if parent is None:
            continue
        key = _parent_dedupe_key(parent)
        if key in seen:
            continue
        seen.add(key)
        parents.append(parent)
    formula = None
    for row in rows:
        formula = _clean(row.get("entered_classterm")) or _clean(row.get("classterm"))
        if formula:
            break
    return _order_parents_by_formula(parents, formula)


def determination_is_hybrid(
    row: dict[str, Any], parents: list[dict[str, Any]] | None = None
) -> bool:
    """Hybrid when the formula has a hybrid marker, or ≥2 parent links exist."""
    if classterm_looks_hybrid(
        row.get("entered_classterm"),
        row.get("classterm"),
        row.get("valid_classterm"),
    ):
        return True
    return len(parents or []) >= 2


def entered_taxon_name_for_determination(row: dict[str, Any]) -> str | None:
    """Prefer verbatim entered classterm; fall back to classterm formula."""
    return _clean(row.get("entered_classterm")) or _clean(row.get("classterm"))


def hybrid_parents_display(
    parents: list[dict[str, Any]], *, max_len: int = 128
) -> str | None:
    """Short parent list for ``Determination.text5`` (varchar 128)."""
    parts: list[str] = []
    for parent in parents:
        name = _clean(parent.get("full_name")) or _clean(parent.get("latin_name"))
        if not name:
            continue
        precision = _clean(parent.get("precision_code"))
        if precision:
            name = f"{name} ({precision})"
        parts.append(name)
    if not parts:
        return None
    text = "; ".join(parts)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip(" ;") + "…"


def hybrid_archive_for_det_key(
    det_rows: list[dict[str, Any]], det_key: tuple
) -> dict[str, Any] | None:
    """Build catchall payload for one determination when it is a hybrid."""
    group = [r for r in det_rows if determination_dedupe_key(r) == det_key]
    if not group:
        return None
    parents = hybrid_parents_from_rows(group)
    representative = group[0]
    if not determination_is_hybrid(representative, parents):
        return None

    class_term_id = representative.get("class_term_id")
    class_event_id = representative.get("class_event_id")
    return {
        "class_event_id": (
            int(class_event_id) if class_event_id is not None else None
        ),
        "class_term_id": int(class_term_id) if class_term_id is not None else None,
        "is_hybrid": True,
        "entered_classterm": _clean(representative.get("entered_classterm")),
        "classterm": _clean(representative.get("classterm")),
        "valid_classterm": _clean(representative.get("valid_classterm")),
        "parents": parents,
    }


def classification_hybrid_archives(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """One hybrid archive entry per determination key (for ``CollectionObject.text3``)."""
    archives: list[dict[str, Any]] = []
    seen_keys: set[tuple] = set()
    for row in rows:
        if row.get("class_event_id") is None:
            continue
        det_key = determination_dedupe_key(row)
        if det_key in seen_keys:
            continue
        seen_keys.add(det_key)
        archive = hybrid_archive_for_det_key(rows, det_key)
        if archive is not None:
            archives.append(archive)
    return archives
