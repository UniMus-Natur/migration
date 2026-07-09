"""Map MUSIT catalog / leg numbers to Specify ``CollectionObject`` fields."""

from __future__ import annotations

from typing import Any

FIELDNUMBER_MAX_LEN = 50


def coerce_musit_identifier_num(value: Any) -> int | None:
    """Return ``IDENTIFIER_NUM`` as int when it is a whole number."""
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not num.is_integer():
        return None
    return int(num)


def resolve_field_number_from_legnr_rows(
    rows: list[dict[str, Any]],
    *,
    primary_actor_id: int | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """Pick collector field number from ``MUSEUM_OBJECT_LEGNR_PERSON`` rows.

    Prefer the primary collector's legnr; fall back to a single row or join
    distinct values when several collectors each have their own number.
    """
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for row in rows:
        legnr = row.get("legnr")
        if legnr is None:
            continue
        text = str(legnr).strip()
        if not text:
            continue
        actor_id = row.get("actor_id")
        key = (actor_id, text)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({"actor_id": actor_id, "legnr": text})

    if not cleaned:
        return None, {"source": None}

    if primary_actor_id is not None:
        for row in cleaned:
            if row.get("actor_id") == primary_actor_id:
                return row["legnr"], {
                    "source": "primary_collector",
                    "actor_id": primary_actor_id,
                }

    if len(cleaned) == 1:
        return cleaned[0]["legnr"], {
            "source": "single_legnr_row",
            "actor_id": cleaned[0].get("actor_id"),
        }

    distinct_legnrs: list[str] = []
    seen_legnr: set[str] = set()
    for row in cleaned:
        text = row["legnr"]
        if text not in seen_legnr:
            seen_legnr.add(text)
            distinct_legnrs.append(text)

    if len(distinct_legnrs) == 1:
        return distinct_legnrs[0], {
            "source": "deduped_single",
            "rows": cleaned,
        }

    combined = "; ".join(distinct_legnrs)[:FIELDNUMBER_MAX_LEN]
    return combined, {
        "source": "multi_collector_join",
        "legnrs": distinct_legnrs,
        "rows": cleaned,
    }
