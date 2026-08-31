"""Resolve MUSIT event person roles into ordered collector/determiner slots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COLLECTOR_ROLE_TERMS: frozenset[str] = frozenset({"LEG", "LEGSCR"})
DETERMINER_ROLE_TERMS: frozenset[str] = frozenset({"DET", "DETSCR"})
SCR_ROLE_TERMS: frozenset[str] = frozenset({"LEGSCR", "DETSCR"})


@dataclass(frozen=True)
class EventPersonRole:
    """One Leg/Det person slot on a collecting or classification event."""

    actor_id: int
    sorting_sequence: int | None
    is_scr: bool


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


def ordernumbers_for_roles(roles: list[EventPersonRole]) -> list[int]:
    """Map MUSIT ``sorting_sequence`` values to Specify ``ordernumber``.

    Rows with a null sort are appended after the highest explicit sequence,
    preserving Oracle ``NULLS LAST`` ordering within the role list.
    """
    max_sort = 0
    for role in roles:
        if role.sorting_sequence is not None:
            max_sort = max(max_sort, int(role.sorting_sequence))
    null_idx = 0
    orders: list[int] = []
    for role in roles:
        if role.sorting_sequence is not None:
            orders.append(int(role.sorting_sequence))
        else:
            null_idx += 1
            orders.append(max_sort + null_idx)
    return orders


def fetch_event_person_roles(
    oracle_cursor: Any,
    schema: str,
    event_id: int,
    *,
    role_terms: frozenset[str],
) -> list[EventPersonRole]:
    """Return ordered unique Leg/Det slots from ``EVENT_ROLE_PERSON_NAME``."""
    if not role_terms:
        return []
    sch = str(schema).strip().upper()
    placeholders = ", ".join(f":r{i}" for i in range(len(role_terms)))
    binds: dict[str, Any] = {"eid": int(event_id)}
    for i, term in enumerate(sorted(role_terms)):
        binds[f"r{i}"] = term

    oracle_cursor.execute(
        f"""
        SELECT pn.actor_id,
               erpn.sorting_sequence,
               UPPER(r.roleterm) AS roleterm
          FROM {sch}.event_role_person_name erpn
          JOIN {sch}.person_name pn
            ON pn.person_name_id = erpn.person_name_id
          JOIN {sch}.roles r
            ON r.role_id = erpn.role_id
         WHERE erpn.event_id = :eid
           AND pn.actor_id IS NOT NULL
           AND UPPER(r.roleterm) IN ({placeholders})
         ORDER BY erpn.sorting_sequence NULLS LAST, erpn.event_person_name_role_id
        """,
        binds,
    )

    ordered: list[EventPersonRole] = []
    seen: set[int] = set()
    for actor_id, sorting_sequence, roleterm in oracle_cursor.fetchall():
        if actor_id is None:
            continue
        aid = int(actor_id)
        if aid in seen:
            continue
        seen.add(aid)
        sort_val: int | None
        if sorting_sequence is None:
            sort_val = None
        else:
            sort_val = int(sorting_sequence)
        role_upper = str(roleterm or "").strip().upper()
        ordered.append(
            EventPersonRole(
                actor_id=aid,
                sorting_sequence=sort_val,
                is_scr=role_upper in SCR_ROLE_TERMS,
            )
        )
    return ordered


def fetch_collector_roles(
    oracle_cursor: Any,
    schema: str,
    event_id: int,
) -> list[EventPersonRole]:
    """Return ordered Leg/LegScr slots for one collecting event."""
    return fetch_event_person_roles(
        oracle_cursor,
        schema,
        event_id,
        role_terms=COLLECTOR_ROLE_TERMS,
    )


def fetch_determiner_roles(
    oracle_cursor: Any,
    schema: str,
    event_id: int,
) -> list[EventPersonRole]:
    """Return ordered Det/DetScr slots for one classification event."""
    return fetch_event_person_roles(
        oracle_cursor,
        schema,
        event_id,
        role_terms=DETERMINER_ROLE_TERMS,
    )


def fetch_event_role_actor_ids(
    oracle_cursor: Any,
    schema: str,
    event_id: int,
) -> list[int]:
    """Return ordered unique ``actor_id`` values from all person-name event roles."""
    roles = fetch_event_person_roles(
        oracle_cursor,
        schema,
        event_id,
        role_terms=COLLECTOR_ROLE_TERMS | DETERMINER_ROLE_TERMS,
    )
    return [role.actor_id for role in roles]


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


def classification_determiner_roles_for_det_key(
    det_rows: list[dict[str, Any]],
    det_key: tuple,
    oracle_cursor: Any,
    schema: str,
) -> list[EventPersonRole]:
    """Return ordered unique determiner slots for one determination envelope."""
    ordered: list[EventPersonRole] = []
    seen: set[int] = set()
    for event_id in classification_event_ids_for_det_key(det_rows, det_key):
        for role in fetch_determiner_roles(oracle_cursor, schema, event_id):
            if role.actor_id not in seen:
                seen.add(role.actor_id)
                ordered.append(role)
    return ordered


def classification_determiner_actor_ids_for_det_key(
    det_rows: list[dict[str, Any]],
    det_key: tuple,
    oracle_cursor: Any,
    schema: str,
) -> list[int]:
    """Return ordered unique determiner ``actor_id`` values for one determination envelope."""
    return [
        role.actor_id
        for role in classification_determiner_roles_for_det_key(
            det_rows, det_key, oracle_cursor, schema
        )
    ]
