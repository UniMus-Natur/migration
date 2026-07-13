"""MUSIT admin geography hierarchy queries (``MV_HIERARKISK_STED``; matches UI ``V_ADMPLACE``)."""

from __future__ import annotations

from typing import Any

_TYPES_LABEL_COLUMN_PRIORITY = (
    "TYPE_NAME",
    "TYPENAME",
    "TYPELABEL",
    "LABEL",
    "DESCRIPTION",
    "TEXT",
    "TYPE_TEXT",
    "CODE",
    "VALUE",
    "NAME",
)


def _norm_type(s: str | None) -> str:
    return (s or "").strip().lower()


def hierarchical_admin_relation(owner: str) -> str:
    """Oracle relation for MUSIT admin geography."""
    return f"{owner.upper()}.MV_HIERARKISK_STED"


def types_label_column_name(cur: Any, owner: str) -> str | None:
    """Return first matching ``TYPES`` column name for hierarchy type labels, or ``None``."""
    o = owner.upper()
    cur.execute(
        """
        SELECT column_name FROM all_tab_columns
        WHERE owner = :owner AND table_name = 'TYPES'
        """,
        {"owner": o},
    )
    existing = {str(r[0]).upper() for r in cur.fetchall()}
    for cand in _TYPES_LABEL_COLUMN_PRIORITY:
        if cand in existing:
            return cand
    return None


def oracle_type_name_to_rank_item_name(type_name: str | None) -> str:
    """Map MUSIT ``TYPES`` label to a logical Specify geography rank name (English)."""
    t = _norm_type(type_name)
    if not t:
        return ""
    if "kommune" in t or "kommun" in t:
        return "Municipality"
    if "fylke" in t:
        return "County"
    if "land" in t and "fylke" not in t:
        return "Country"
    if "kontinent" in t or "continent" in t:
        return "Continent"
    if "region" in t or "del" in t:
        return "State"
    return "County"


def fetch_hierarchical_chain_rows_for_place(
    oracle_cursor: Any,
    owner: str,
    place_id: int,
) -> list[dict[str, Any]]:
    """Return admin geography rows for a ``PLACE_ID``, walking ancestors to root."""
    o = owner.upper()
    rel = hierarchical_admin_relation(owner)
    label_col = types_label_column_name(oracle_cursor, owner)
    type_expr = f"t.{label_col}" if label_col else "CAST(NULL AS VARCHAR2(4000))"

    oracle_cursor.execute(
        f"SELECT php.HIERACHICAL_PLACE_ID FROM {o}.place_hierachical_place php WHERE php.place_id = :pid",
        {"pid": place_id},
    )
    seed_ids = [int(r[0]) for r in oracle_cursor.fetchall() if r and r[0] is not None]
    if not seed_ids:
        return []

    by_hid: dict[int, dict[str, Any]] = {}
    queue: list[int] = list(seed_ids)
    seen: set[int] = set()
    while queue:
        hid = int(queue.pop())
        if hid in seen:
            continue
        seen.add(hid)
        oracle_cursor.execute(
            f"""
            SELECT h.HIERARCH_PLACE_ID, h.HIERACHICAL_PLACENAME, h.PLACE_ID_PARTOF, {type_expr} AS TYPE_NAME
              FROM {rel} h
              LEFT JOIN {o}.types t ON t.TYPE_ID = h.HIERACHICAL_TYPE
             WHERE h.HIERARCH_PLACE_ID = :hid
            """,
            {"hid": hid},
        )
        row = oracle_cursor.fetchone()
        if not row:
            continue
        partof = int(row[2]) if row[2] is not None else None
        by_hid[hid] = {
            "hid": hid,
            "name": ((row[1] or "").strip() or f"ID_{hid}")[:128],
            "partof": partof,
            "type_name": row[3],
        }
        if partof is not None and partof not in seen:
            queue.append(partof)

    ordered: list[dict[str, Any]] = []
    remaining = set(by_hid.keys())
    ordered_ids: set[int] = set()
    guard = 0
    while remaining and guard < (len(remaining) + 5):
        guard += 1
        progressed = False
        for hid in list(remaining):
            parent = by_hid[hid]["partof"]
            if parent is None or parent not in by_hid or parent in ordered_ids:
                ordered.append(by_hid[hid])
                ordered_ids.add(hid)
                remaining.remove(hid)
                progressed = True
        if not progressed:
            for hid in list(remaining):
                ordered.append(by_hid[hid])
                ordered_ids.add(hid)
                remaining.remove(hid)
    return ordered
