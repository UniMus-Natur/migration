"""Resolve MUSIT classification-event roles into ordered determiner actor IDs."""

from __future__ import annotations

from typing import Any


def _trunc(s: Any, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    return (t[:max_len] if len(t) > max_len else t) or None


def determination_dedupe_key(r: dict[str, Any]) -> tuple:
    """Identity for one Specify ``Determination``.

    Prefer MUSIT ``classification_event.event_id`` so each classification event
    (including same taxon on different dates) becomes its own determination.
    Join fan-out on a single event still collapses to one row.

    Fallback (no ``class_event_id``) keeps taxon text fields plus determination
    date so same-taxon / different-date rows are not merged.
    """
    eid = r.get("class_event_id")
    if eid is not None:
        try:
            return ("event", int(eid))
        except (TypeError, ValueError):
            pass
    return (
        "taxon",
        r.get("adb_taxon_id"),
        r.get("adb_latin_name_id"),
        r.get("latin_name_id"),
        _trunc(r.get("valid_classterm"), 255),
        _trunc(r.get("classterm"), 255),
        str(r["class_from_date"]) if r.get("class_from_date") is not None else None,
        str(r["class_to_date"]) if r.get("class_to_date") is not None else None,
        _trunc(r.get("class_time_as_text"), 64),
    )


def classification_event_ids_for_det_key(
    det_rows: list[dict[str, Any]], det_key: tuple
) -> list[int]:
    """Return classification ``event_id`` values for rows sharing a determination key."""
    event_ids: list[int] = []
    seen: set[int] = set()
    for r in det_rows:
        if determination_dedupe_key(r) != det_key:
            continue
        eid = r.get("class_event_id")
        if eid is None:
            continue
        eid_int = int(eid)
        if eid_int not in seen:
            seen.add(eid_int)
            event_ids.append(eid_int)
    return event_ids


def fetch_event_role_actor_ids(
    oracle_cursor: Any,
    schema: str,
    event_id: int,
) -> list[int]:
    """Return ordered unique ``actor_id`` values from person-name and actor event roles."""
    sch = str(schema).strip().upper()
    ordered: list[int] = []
    seen: set[int] = set()

    oracle_cursor.execute(
        f"""
        SELECT pn.actor_id
          FROM {sch}.event_role_person_name erpn
          JOIN {sch}.person_name pn
            ON pn.person_name_id = erpn.person_name_id
         WHERE erpn.event_id = :eid
           AND pn.actor_id IS NOT NULL
         ORDER BY erpn.sorting_sequence NULLS LAST, erpn.event_person_name_role_id
        """,
        {"eid": int(event_id)},
    )
    for (actor_id,) in oracle_cursor.fetchall():
        aid = int(actor_id)
        if aid not in seen:
            seen.add(aid)
            ordered.append(aid)

    oracle_cursor.execute(
        f"""
        SELECT era.actor_id
          FROM {sch}.event_role_actor era
         WHERE era.event_id = :eid
           AND era.actor_id IS NOT NULL
         ORDER BY era.event_actor_role_id
        """,
        {"eid": int(event_id)},
    )
    for (actor_id,) in oracle_cursor.fetchall():
        aid = int(actor_id)
        if aid not in seen:
            seen.add(aid)
            ordered.append(aid)

    return ordered


def fetch_actor_display_names(
    oracle_cursor: Any,
    schema: str,
    actor_ids: list[int],
) -> dict[int, str]:
    """Return ``{actor_id: display name}`` using MUSIT ``ACTOR.ACTORNAME`` fallback."""
    if not actor_ids:
        return {}
    sch = str(schema).strip().upper()
    placeholders = ", ".join(f":a{i}" for i in range(len(actor_ids)))
    binds = {f"a{i}": int(aid) for i, aid in enumerate(actor_ids)}
    oracle_cursor.execute(
        f"""
        SELECT a.actor_id,
               COALESCE(
                 NULLIF(TRIM(a.actorname), ''),
                 NULLIF(TRIM(pn.person_surname || ', ' || pn.person_given_name), ','),
                 NULLIF(TRIM(pn.person_given_name || ' ' || pn.person_surname), '')
               ) AS display_name
          FROM {sch}.actor a
          LEFT JOIN {sch}.person_name pn
            ON pn.person_name_id = NVL(
              a.valid_person_name_id,
              (SELECT MIN(pn2.person_name_id)
                 FROM {sch}.person_name pn2
                WHERE pn2.actor_id = a.actor_id)
            )
         WHERE a.actor_id IN ({placeholders})
        """,
        binds,
    )
    out: dict[int, str] = {}
    for actor_id, display_name in oracle_cursor.fetchall():
        if actor_id is None:
            continue
        name = str(display_name).strip() if display_name is not None else ""
        if name:
            out[int(actor_id)] = name
    return out


def classification_determiner_actor_ids_for_det_key(
    det_rows: list[dict[str, Any]],
    det_key: tuple,
    oracle_cursor: Any,
    schema: str,
) -> list[int]:
    """Return ordered unique determiner ``actor_id`` values for one determination envelope."""
    ordered: list[int] = []
    seen: set[int] = set()
    for event_id in classification_event_ids_for_det_key(det_rows, det_key):
        for actor_id in fetch_event_role_actor_ids(oracle_cursor, schema, event_id):
            if actor_id not in seen:
                seen.add(actor_id)
                ordered.append(actor_id)
    return ordered
