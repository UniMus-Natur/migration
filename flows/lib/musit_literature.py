"""MUSIT literature → Specify archive JSON and type-publication ReferenceWorks.

Specimen literature (``DOCUMENT_OBJECT``) and taxon literature
(``EVENT_DOCUMENT`` on classification events) are archived as JSON and
promoted to CO text fields (``ocr`` / ``embargoreason``). Type-info
publication (``EVENT_DOCUMENT`` on typification events) becomes a
``ReferenceWork`` linked via ``CollectionObjectCitation`` on the specimen.
"""

from __future__ import annotations

import re
from typing import Any

# Book=0, Electronic Media=1, Paper=2, Technical Report=3, Thesis=4, Section=5
_REFERENCE_WORK_TYPE_PAPER = 2

_YEAR_RE = re.compile(r"\b((?:1[5-9]|20)\d{2})\b")


def _trunc(s: Any, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    return (t[:max_len] if len(t) > max_len else t) or None


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    t = str(value).strip()
    return t or None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def musit_document_guid(document_id: int) -> str:
    return f"urn:oracle:musit:document:{int(document_id)}"[:128]


def extract_work_date(*, from_date: Any = None, time_as_text: Any = None) -> str | None:
    """Prefer a 4-digit year from ``TIMESPAN.TIME_AS_TEXT``, else ``FROM_DATE`` year."""
    text = _nonempty_text(time_as_text)
    if text:
        matches = _YEAR_RE.findall(text)
        if matches:
            return matches[-1][:25]
        truncated = _trunc(text, 25)
        if truncated:
            return truncated
    if from_date is not None:
        iso = str(from_date)
        if len(iso) >= 4 and iso[:4].isdigit():
            return iso[:4]
    return None


def _doc_has_content(reference: Any, title: Any) -> bool:
    return _nonempty_text(reference) is not None or _nonempty_text(title) is not None


def literature_archive_payload(bundle: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """Return the JSON-serialisable archive blob, or None when there is nothing to keep."""
    specimen = list(bundle.get("specimen") or [])
    taxon = list(bundle.get("taxon") or [])
    type_info = list(bundle.get("type_info") or [])
    if not specimen and not taxon and not type_info:
        return None
    out: dict[str, Any] = {}
    if specimen:
        out["specimen_literature"] = specimen
    if taxon:
        out["taxon_literature"] = taxon
    if type_info:
        out["type_info"] = type_info
    return out


def taxon_literature_for_event_ids(
    taxon_rows: list[dict[str, Any]], event_ids: list[int]
) -> list[dict[str, Any]]:
    wanted = {int(eid) for eid in event_ids}
    return [row for row in taxon_rows if _int_or_none(row.get("event_id")) in wanted]


def _oracle_in(ids: list[int], prefix: str = "id") -> tuple[str, dict[str, int]]:
    placeholders = ", ".join(f":{prefix}{i}" for i in range(len(ids)))
    binds = {f"{prefix}{i}": int(oid) for i, oid in enumerate(ids)}
    return placeholders, binds


def _rows_from_cursor(oracle_cursor: Any) -> list[dict[str, Any]]:
    cols = [d[0].lower() for d in oracle_cursor.description]
    return [dict(zip(cols, row)) for row in oracle_cursor.fetchall()]


def fetch_literature_for_objects(
    oracle_cursor: Any,
    schema: str,
    object_ids: list[int],
    *,
    batch_size: int = 500,
) -> dict[int, dict[str, list[dict[str, Any]]]]:
    """Batch-load specimen, taxon, and type-info literature for a page of objects."""
    empty: dict[str, list[dict[str, Any]]] = {
        "specimen": [],
        "taxon": [],
        "type_info": [],
    }
    out: dict[int, dict[str, list[dict[str, Any]]]] = {}
    if not object_ids:
        return out
    sch = str(schema).strip().upper()
    unique_ids = list(dict.fromkeys(int(oid) for oid in object_ids))
    for start in range(0, len(unique_ids), batch_size):
        chunk = unique_ids[start : start + batch_size]
        placeholders, binds = _oracle_in(chunk, "oid")
        _fetch_specimen_chunk(oracle_cursor, sch, placeholders, binds, out)
        _fetch_taxon_chunk(oracle_cursor, sch, placeholders, binds, out)
        _fetch_type_info_chunk(oracle_cursor, sch, placeholders, binds, out)
    for oid in unique_ids:
        out.setdefault(oid, {k: list(v) for k, v in empty.items()})
    return out


def _ensure_bucket(
    out: dict[int, dict[str, list[dict[str, Any]]]], object_id: int
) -> dict[str, list[dict[str, Any]]]:
    bucket = out.get(object_id)
    if bucket is None:
        bucket = {"specimen": [], "taxon": [], "type_info": []}
        out[object_id] = bucket
    return bucket


def _fetch_specimen_chunk(
    oracle_cursor: Any,
    schema: str,
    placeholders: str,
    binds: dict[str, int],
    out: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    oracle_cursor.execute(
        f"""
        SELECT do.object_id, rd.document_id, rd.document_reference, rd.document_title
          FROM {schema}.document_object do
          JOIN {schema}.reference_document rd
            ON rd.document_id = do.document_id
         WHERE do.object_id IN ({placeholders})
         ORDER BY do.object_id, do.document_object_id
        """,
        binds,
    )
    seen: set[tuple[int, int]] = set()
    for row in _rows_from_cursor(oracle_cursor):
        if not _doc_has_content(row.get("document_reference"), row.get("document_title")):
            continue
        oid = _int_or_none(row.get("object_id"))
        doc_id = _int_or_none(row.get("document_id"))
        if oid is None or doc_id is None:
            continue
        key = (oid, doc_id)
        if key in seen:
            continue
        seen.add(key)
        _ensure_bucket(out, oid)["specimen"].append(
            {
                "document_id": doc_id,
                "reference": _nonempty_text(row.get("document_reference")),
                "title": _nonempty_text(row.get("document_title")),
            }
        )


def _fetch_taxon_chunk(
    oracle_cursor: Any,
    schema: str,
    placeholders: str,
    binds: dict[str, int],
    out: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    oracle_cursor.execute(
        f"""
        SELECT emo.object_id, ce.event_id, rd.document_id,
               rd.document_reference, rd.document_title
          FROM {schema}.event_museum_object emo
          JOIN {schema}.classification_event ce
            ON ce.event_id = emo.event_id
          JOIN {schema}.event_document ed
            ON ed.event_id = ce.event_id
          JOIN {schema}.reference_document rd
            ON rd.document_id = ed.document_id
         WHERE emo.object_id IN ({placeholders})
         ORDER BY emo.object_id, ce.event_id, ed.event_document_id
        """,
        binds,
    )
    seen: set[tuple[int, int, int]] = set()
    for row in _rows_from_cursor(oracle_cursor):
        if not _doc_has_content(row.get("document_reference"), row.get("document_title")):
            continue
        oid = _int_or_none(row.get("object_id"))
        event_id = _int_or_none(row.get("event_id"))
        doc_id = _int_or_none(row.get("document_id"))
        if oid is None or event_id is None or doc_id is None:
            continue
        key = (oid, event_id, doc_id)
        if key in seen:
            continue
        seen.add(key)
        _ensure_bucket(out, oid)["taxon"].append(
            {
                "document_id": doc_id,
                "event_id": event_id,
                "reference": _nonempty_text(row.get("document_reference")),
                "title": _nonempty_text(row.get("document_title")),
            }
        )


def _fetch_type_info_chunk(
    oracle_cursor: Any,
    schema: str,
    placeholders: str,
    binds: dict[str, int],
    out: dict[int, dict[str, list[dict[str, Any]]]],
) -> None:
    oracle_cursor.execute(
        f"""
        SELECT emo.object_id,
               te.event_id,
               rd.document_id,
               rd.document_reference,
               rd.document_title,
               n.note_text,
               ts.from_date,
               ts.time_as_text
          FROM {schema}.event_museum_object emo
          JOIN {schema}.typification_event te
            ON te.event_id = emo.event_id
          LEFT JOIN {schema}.event_document ed
            ON ed.event_id = te.event_id
          LEFT JOIN {schema}.reference_document rd
            ON rd.document_id = ed.document_id
          LEFT JOIN {schema}.event_note en
            ON en.event_id = te.event_id
          LEFT JOIN {schema}.note n
            ON n.note_id = en.note_id
          LEFT JOIN {schema}.event ev
            ON ev.event_id = te.event_id
          LEFT JOIN {schema}.timespan ts
            ON ts.timespan_id = ev.timespan_id
         WHERE emo.object_id IN ({placeholders})
         ORDER BY emo.object_id, te.event_id, ed.event_document_id, en.event_note_id
        """,
        binds,
    )
    merged: dict[tuple[int, int, int | None], dict[str, Any]] = {}
    order: list[tuple[int, int, int | None]] = []
    for row in _rows_from_cursor(oracle_cursor):
        oid = _int_or_none(row.get("object_id"))
        event_id = _int_or_none(row.get("event_id"))
        if oid is None or event_id is None:
            continue
        doc_id = _int_or_none(row.get("document_id"))
        key = (oid, event_id, doc_id)
        entry = merged.get(key)
        if entry is None:
            entry = {
                "event_id": event_id,
                "document_id": doc_id,
                "reference": _nonempty_text(row.get("document_reference")),
                "title": _nonempty_text(row.get("document_title")),
                "note": _nonempty_text(row.get("note_text")),
                "work_date": extract_work_date(
                    from_date=row.get("from_date"),
                    time_as_text=row.get("time_as_text"),
                ),
                "from_date": str(row["from_date"]) if row.get("from_date") is not None else None,
                "time_as_text": _nonempty_text(row.get("time_as_text")),
            }
            merged[key] = entry
            order.append(key)
        else:
            note = _nonempty_text(row.get("note_text"))
            if note and note not in (entry.get("note") or ""):
                entry["note"] = (
                    f"{entry['note']}; {note}" if entry.get("note") else note
                )
            if entry.get("work_date") is None:
                entry["work_date"] = extract_work_date(
                    from_date=row.get("from_date"),
                    time_as_text=row.get("time_as_text"),
                )
            if entry.get("reference") is None:
                entry["reference"] = _nonempty_text(row.get("document_reference"))
            if entry.get("title") is None:
                entry["title"] = _nonempty_text(row.get("document_title"))
    for oid, _event_id, _doc_id in order:
        entry = merged[(oid, _event_id, _doc_id)]
        if (
            entry.get("document_id") is None
            and entry.get("reference") is None
            and entry.get("title") is None
            and entry.get("note") is None
            and entry.get("work_date") is None
        ):
            continue
        _ensure_bucket(out, oid)["type_info"].append(entry)


def reference_work_title(entry: dict[str, Any]) -> str | None:
    return _trunc(entry.get("reference") or entry.get("title"), 500)


def attach_type_publications_to_collection_object(
    *,
    collection_object: Any,
    type_info: list[dict[str, Any]],
    institution: Any,
    collection_id: int,
    stats: Any,
) -> int:
    """Create ``ReferenceWork`` + ``CollectionObjectCitation`` rows for type publications.

    Does not deduplicate by title; reuses an existing row with the same MUSIT
    ``document_id`` GUID so remigration is idempotent.
    """
    from specifyweb.specify.models import Collectionobjectcitation, Referencework

    created = 0
    seen_docs: set[int] = set()
    for entry in type_info:
        doc_id = _int_or_none(entry.get("document_id"))
        title = reference_work_title(entry)
        if doc_id is None or not title:
            continue
        if doc_id in seen_docs:
            continue
        seen_docs.add(doc_id)
        guid = musit_document_guid(doc_id)
        rw = Referencework.objects.filter(guid=guid).first()
        if rw is None:
            rw = Referencework(
                guid=guid,
                title=title,
                referenceworktype=_REFERENCE_WORK_TYPE_PAPER,
                institution=institution,
                workdate=_trunc(entry.get("work_date"), 25),
            )
            rw.save()
            if hasattr(stats, "referencework_created"):
                stats.referencework_created += 1
        citation, was_created = Collectionobjectcitation.objects.get_or_create(
            collectionobject=collection_object,
            referencework=rw,
            defaults={
                "collectionmemberid": int(collection_id),
                "remarks": _trunc(entry.get("note"), 4000),
            },
        )
        if was_created:
            created += 1
            if hasattr(stats, "collectionobject_citation_created"):
                stats.collectionobject_citation_created += 1
        elif entry.get("note") and not citation.remarks:
            Collectionobjectcitation.objects.filter(pk=citation.pk).update(
                remarks=_trunc(entry.get("note"), 4000)
            )
    return created
