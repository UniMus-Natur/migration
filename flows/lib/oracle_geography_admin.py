"""MUSIT admin geography hierarchy queries (``MV_HIERARKISK_STED``; matches UI ``V_ADMPLACE``)."""

from __future__ import annotations

from dataclasses import dataclass
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


# Synthetic Oracle planet/world rows must alias to Specify Earth, not become a Continent node.
WORLD_SHELL_NAMES: frozenset[str] = frozenset(
    {
        "world",
        "the world",
        "verden",
        "whole world",
        "global",
    }
)


@dataclass(frozen=True)
class NorwegianGeographyRankSpec:
    """Linear Specify geography ladder matching MUSIT ``TYPES`` (nodes may skip ranks)."""

    rankid: int
    name: str
    isinfullname: bool
    isenforced: bool
    aliases: tuple[str, ...] = ()


# Rankids are strictly increasing so any MUSIT parent→child edge is legal in Specify.
# Core ranks are land-admin columns with no gaps. Optional marine/region ranks are added on
# demand only — otherwise untyped rows fall through into Ocean/Sea columns in Specify UI.
NORWEGIAN_GEOGRAPHY_CORE_RANKS: tuple[NorwegianGeographyRankSpec, ...] = (
    NorwegianGeographyRankSpec(0, "Earth", False, True, ("Planet", "World")),
    NorwegianGeographyRankSpec(100, "Continent", False, False),
    NorwegianGeographyRankSpec(200, "Land", False, False, ("Country",)),
    NorwegianGeographyRankSpec(300, "Fylke", True, False, ("State",)),
    NorwegianGeographyRankSpec(400, "Kommune", True, False, ("County",)),
    NorwegianGeographyRankSpec(500, "Gammel kommune", True, False, ("Municipality",)),
    NorwegianGeographyRankSpec(600, "Sted", True, False, ("Settlement",)),
    NorwegianGeographyRankSpec(700, "Place", True, False),
)

NORWEGIAN_GEOGRAPHY_OPTIONAL_RANKS: tuple[NorwegianGeographyRankSpec, ...] = (
    NorwegianGeographyRankSpec(150, "Ocean", False, False),
    NorwegianGeographyRankSpec(180, "Sea", False, False),
    NorwegianGeographyRankSpec(320, "Gammelt fylke", True, False),
    NorwegianGeographyRankSpec(350, "Region", True, False),
    NorwegianGeographyRankSpec(370, "Sub region", True, False),
)

NORWEGIAN_GEOGRAPHY_RANKS: tuple[NorwegianGeographyRankSpec, ...] = (
    NORWEGIAN_GEOGRAPHY_CORE_RANKS + NORWEGIAN_GEOGRAPHY_OPTIONAL_RANKS
)

GEOGRAPHY_FULLNAME_SEPARATOR = ", "

# Untyped Oracle rows use this land-admin sequence (never Ocean/Sea gap rankids).
LAND_ADMIN_FALLBACK_RANKIDS: tuple[int, ...] = tuple(spec.rankid for spec in NORWEGIAN_GEOGRAPHY_CORE_RANKS)


def oracle_row_is_world_or_planet_shell(name: str | None, type_name: str | None = None) -> bool:
    """True when this hierarchical row is MUSIT's synthetic global shell (WORLD / Planet / …)."""
    if _norm_type(name) in WORLD_SHELL_NAMES:
        return True
    return oracle_type_name_to_rank_item_name(type_name) == "Earth"


def should_alias_geography_to_parent(
    *,
    child_name: str | None,
    parent_name: str | None,
    parent_is_earth: bool,
) -> bool:
    """Skip inserting a geography node that only repeats its parent's name.

    Untyped Oracle chains repeat kommune names (Holmestrand×3). Typed chains repeat
    current kommune as ``Gammel kommune`` (Tønsberg under Tønsberg) so a nested
    historical unit (Sem) can sit at the Gammel kommune rank instead of overflowing
    to Sted. Never alias onto Earth.
    """
    if parent_is_earth:
        return False
    child = (child_name or "").strip()
    parent = (parent_name or "").strip()
    return bool(child and parent and child.casefold() == parent.casefold())


def oracle_type_name_to_rank_item_name(type_name: str | None) -> str:
    """Map MUSIT ``TYPES`` label to a Specify ``GeographyTreeDefItem`` name.

    Historical labels (``Gammel kommune``, ``Gammelt fylke``, ``Sub region``) must be
    checked before the current-admin substring they contain.
    """
    t = _norm_type(type_name)
    if not t:
        return ""
    if t in {"planet", "earth", "world"}:
        return "Earth"
    if "kontinent" in t or "continent" in t:
        return "Continent"
    if t in {"ocean", "hav"}:
        return "Ocean"
    if t in {"sea", "sjø", "sjo"}:
        return "Sea"
    if "gammelt fylke" in t:
        return "Gammelt fylke"
    if "fylke" in t:
        return "Fylke"
    if "sub region" in t or "sub-region" in t or t in {"subregion", "delregion"}:
        return "Sub region"
    if "region" in t:
        return "Region"
    if "gammel kommune" in t:
        return "Gammel kommune"
    if "kommune" in t or t == "kommun":
        return "Kommune"
    if t == "land":
        return "Land"
    return ""


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
