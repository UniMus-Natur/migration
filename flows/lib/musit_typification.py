"""MUSIT typification + Literature-tab text → CollectionObject fields.

Type metadata (status, designators, year, note) and Literature-tab free text
are promoted to first-class CO fields. Structured detail remains in
``CollectionObject.text3`` JSON; type publications become ``ReferenceWork`` rows
linked via ``CollectionObjectCitation``.
"""

from __future__ import annotations

from typing import Any, Callable

from flows.lib.musit_literature import _int_or_none, _nonempty_text, _oracle_in, _rows_from_cursor, _trunc
from flows.lib.musit_type_status import resolve_type_status_name

TYPE_DESIGNATOR_ROLE_ID = 16


def _clob_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "read"):
        value = value.read()
    return _nonempty_text(value)


def _aggregate_reference_lines(rows: list[dict[str, Any]]) -> str | None:
    parts: list[str] = []
    seen: set[str] = set()
    for row in rows:
        ref = _nonempty_text(row.get("reference")) or _nonempty_text(row.get("title"))
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        parts.append(ref)
    return "; ".join(parts) if parts else None


def literature_tab_text_from_bundle(bundle: dict[str, list[dict[str, Any]]]) -> tuple[str | None, str | None]:
    """Build Literature-tab strings from structured document rows when ``V_LITERATURE`` is absent."""
    return (
        _aggregate_reference_lines(list(bundle.get("specimen") or [])),
        _aggregate_reference_lines(list(bundle.get("taxon") or [])),
    )


def parse_typification_year(type_info: list[dict[str, Any]]) -> int | None:
    for entry in type_info:
        work_date = _nonempty_text(entry.get("work_date"))
        if work_date and work_date.isdigit():
            year = int(work_date)
            if 1500 <= year <= 2100:
                return year
        from_date = _nonempty_text(entry.get("from_date"))
        if from_date and len(from_date) >= 4 and from_date[:4].isdigit():
            year = int(from_date[:4])
            if 1500 <= year <= 2100:
                return year
    return None


def merge_typification_notes(type_info: list[dict[str, Any]]) -> str | None:
    parts: list[str] = []
    seen: set[str] = set()
    for entry in type_info:
        note = _nonempty_text(entry.get("note"))
        if note is None or note in seen:
            continue
        seen.add(note)
        parts.append(note)
    return "; ".join(parts) if parts else None


def fetch_typification_meta_for_objects(
    oracle_cursor: Any,
    schema: str,
    object_ids: list[int],
    *,
    batch_size: int = 500,
) -> dict[int, dict[str, Any]]:
    """Batch-load type designators and Literature-tab aggregated text per object."""
    out: dict[int, dict[str, Any]] = {}
    if not object_ids:
        return out
    sch = str(schema).strip().upper()
    unique_ids = list(dict.fromkeys(int(oid) for oid in object_ids))
    for start in range(0, len(unique_ids), batch_size):
        chunk = unique_ids[start : start + batch_size]
        placeholders, binds = _oracle_in(chunk, "oid")
        _fetch_designators_chunk(oracle_cursor, sch, placeholders, binds, out)
        _fetch_v_literature_chunk(oracle_cursor, sch, placeholders, binds, out)
    for oid in unique_ids:
        out.setdefault(
            oid,
            {
                "designator_actor_ids": [],
                "specimen_literature": None,
                "taxon_literature": None,
            },
        )
    return out


def _fetch_designators_chunk(
    oracle_cursor: Any,
    schema: str,
    placeholders: str,
    binds: dict[str, int],
    out: dict[int, dict[str, Any]],
) -> None:
    oracle_cursor.execute(
        f"""
        SELECT emo.object_id,
               te.event_id,
               pn.actor_id,
               erpn.sorting_sequence
          FROM {schema}.event_museum_object emo
          JOIN {schema}.typification_event te
            ON te.event_id = emo.event_id
          JOIN {schema}.event_role_person_name erpn
            ON erpn.event_id = te.event_id
           AND erpn.role_id = {TYPE_DESIGNATOR_ROLE_ID}
          JOIN {schema}.person_name pn
            ON pn.person_name_id = erpn.person_name_id
         WHERE emo.object_id IN ({placeholders})
         ORDER BY emo.object_id, te.event_id DESC, erpn.sorting_sequence
        """,
        binds,
    )
    current_object: int | None = None
    current_event: int | None = None
    actor_ids: list[int] = []
    seen_actors: set[int] = set()
    for row in _rows_from_cursor(oracle_cursor):
        oid = _int_or_none(row.get("object_id"))
        event_id = _int_or_none(row.get("event_id"))
        actor_id = _int_or_none(row.get("actor_id"))
        if oid is None or event_id is None or actor_id is None:
            continue
        if current_object != oid:
            if current_object is not None:
                out[current_object]["designator_actor_ids"] = actor_ids
            current_object = oid
            current_event = event_id
            actor_ids = []
            seen_actors = set()
            out.setdefault(
                current_object,
                {
                    "designator_actor_ids": [],
                    "specimen_literature": None,
                    "taxon_literature": None,
                },
            )
        elif current_event != event_id:
            continue
        if actor_id in seen_actors:
            continue
        seen_actors.add(actor_id)
        actor_ids.append(actor_id)
    if current_object is not None:
        out[current_object]["designator_actor_ids"] = actor_ids


def _fetch_v_literature_chunk(
    oracle_cursor: Any,
    schema: str,
    placeholders: str,
    binds: dict[str, int],
    out: dict[int, dict[str, Any]],
) -> None:
    try:
        oracle_cursor.execute(
            f"""
            SELECT object_id, specimen_literature, taxon_literature
              FROM {schema}.v_literature
             WHERE object_id IN ({placeholders})
            """,
            binds,
        )
    except Exception:
        return
    for row in _rows_from_cursor(oracle_cursor):
        oid = _int_or_none(row.get("object_id"))
        if oid is None:
            continue
        bucket = out.setdefault(
            oid,
            {
                "designator_actor_ids": [],
                "specimen_literature": None,
                "taxon_literature": None,
            },
        )
        specimen = _clob_text(row.get("specimen_literature"))
        taxon = _clob_text(row.get("taxon_literature"))
        if specimen:
            bucket["specimen_literature"] = specimen
        if taxon:
            bucket["taxon_literature"] = taxon


def build_typification_co_field_updates(
    *,
    type_status_raw: Any,
    type_info: list[dict[str, Any]],
    typification_meta: dict[str, Any] | None,
    literature_bundle: dict[str, list[dict[str, Any]]],
    schema: str,
    agent_cache: dict[int, int] | None,
    resolve_agent: Callable[..., Any],
) -> dict[str, Any]:
    """Return CollectionObject field values for typification + Literature tab."""
    meta = typification_meta or {}
    designator_actor_ids = list(meta.get("designator_actor_ids") or [])

    specimen_text = _nonempty_text(meta.get("specimen_literature"))
    taxon_text = _nonempty_text(meta.get("taxon_literature"))
    if specimen_text is None or taxon_text is None:
        fallback_specimen, fallback_taxon = literature_tab_text_from_bundle(literature_bundle)
        if specimen_text is None:
            specimen_text = fallback_specimen
        if taxon_text is None:
            taxon_text = fallback_taxon

    updates: dict[str, Any] = {}
    type_status = resolve_type_status_name(type_status_raw)
    if type_status:
        updates["restrictions"] = _trunc(type_status, 32)

    typification_year = parse_typification_year(type_info)
    if typification_year is not None:
        updates["integer2"] = typification_year

    type_note = merge_typification_notes(type_info)
    if type_note:
        updates["reservedtext3"] = _trunc(type_note, 128)

    if designator_actor_ids:
        agent1 = resolve_agent(schema, designator_actor_ids[0], agent_cache=agent_cache)
        if agent1 is not None:
            updates["agent1"] = agent1
    if len(designator_actor_ids) > 1:
        agent2 = resolve_agent(schema, designator_actor_ids[1], agent_cache=agent_cache)
        if agent2 is not None:
            updates["cataloger"] = agent2

    if specimen_text:
        updates["ocr"] = specimen_text
    if taxon_text:
        updates["embargoreason"] = taxon_text

    return updates


def apply_typification_co_field_updates(co: Any, updates: dict[str, Any]) -> None:
    for field, value in updates.items():
        setattr(co, field, value)
