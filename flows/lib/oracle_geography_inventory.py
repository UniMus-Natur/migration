"""Read-only Oracle counts for MUSIT / USD geolocation objects (Prefect migration support)."""

from __future__ import annotations

from typing import Any


def _count(cur: Any, sql: str) -> int:
    cur.execute(sql)
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def inventory_musit_botany(cur: Any) -> dict[str, int]:
    """Counts under ``MUSIT_BOTANIKK_FELLES`` (adjust if your grants differ)."""
    p = "MUSIT_BOTANIKK_FELLES"
    return {
        f"{p}.PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.place"),
        f"{p}.PLACE_OBJECT_ROLE": _count(cur, f"SELECT COUNT(*) FROM {p}.place_object_role"),
        f"{p}.PLACE_EVENT_ROLE": _count(cur, f"SELECT COUNT(*) FROM {p}.place_event_role"),
        f"{p}.PLACE_LOCALITY_PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.place_locality_place"),
        f"{p}.PLACE_HIERACHICAL_PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.place_hierachical_place"),
        f"{p}.HIERARCHICAL_PLACE_OLD": _count(cur, f"SELECT COUNT(*) FROM {p}.hierarchical_place_old"),
        f"{p}.LOCALITY_PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.locality_place"),
        f"{p}.KOORDINATE_PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.koordinate_place"),
        f"{p}.KOORDINATE_PLACE_PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.koordinate_place_place"),
        f"{p}.ADMINISTRATIVE_PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.administrative_place"),
    }


def inventory_musit_entomology(cur: Any) -> dict[str, int]:
    p = "MUSIT_ZOOLOGI_ENTOMOLOGI"
    return {
        f"{p}.PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.place"),
        f"{p}.PLACE_OBJECT_ROLE": _count(cur, f"SELECT COUNT(*) FROM {p}.place_object_role"),
        f"{p}.KOORDINATE_PLACE": _count(cur, f"SELECT COUNT(*) FROM {p}.koordinate_place"),
    }


def inventory_usd_sample(cur: Any, owner: str) -> dict[str, int]:
    """Best-effort counts for one USD botany schema (ORA-00942 → -1 for that table)."""
    out: dict[str, int] = {}
    o = owner.upper()
    for table in ("ADMINISTRATIVTSTED", "KOORDINATSETT", "GEOREG"):
        try:
            out[f"{o}.{table}"] = _count(cur, f"SELECT COUNT(*) FROM {o}.{table}")
        except Exception:
            out[f"{o}.{table}"] = -1
    return out


def referenced_place_ids_count(cur: Any, owner: str) -> int:
    """Distinct ``PLACE_ID`` values tied to objects or events."""
    o = owner.upper()
    sql = f"""
    SELECT COUNT(*) FROM (
      SELECT place_id FROM {o}.place_object_role
      UNION
      SELECT place_id FROM {o}.place_event_role
    ) x
    """
    return _count(cur, sql)
