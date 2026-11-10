"""Helpers for MUSIT ``PERSON_NAME`` → Specify ``AgentVariant`` fill-in migration.

The Phase 1.1 agents flow stores only the preferred (or fallback) name on ``Agent``.
Alternate ``PERSON_NAME`` rows are loaded later as ``AgentVariant`` (varType Variant=0)
so synonymic / label orthographies are not lost.
"""

from __future__ import annotations

from typing import Any

from flows.lib.musit_agent_names import (
    formatted_person_name_sql,
    fullest_person_name_id_sql,
    preferred_person_name_id_sql,
)

# Specify default picklist: Variant=0, Vernacular=1, Author=2, Author Abbrev.=3, Label Name=4
AGENT_VARIANT_VARTYPE_VARIANT = 0

# AgentVariant.name max length in Specify
_AGENT_VARIANT_NAME_MAX = 255

# Re-export for callers/tests that imported from this module.
__all__ = [
    "AGENT_VARIANT_VARTYPE_VARIANT",
    "format_person_name_variant",
    "preferred_person_name_id_sql",
    "sql_alternate_person_names",
    "should_skip_variant_name",
    "variant_row_from_oracle",
]


def format_person_name_variant(
    surname: str | None,
    given: str | None,
    middle: str | None = None,
    *,
    max_len: int = _AGENT_VARIANT_NAME_MAX,
) -> str | None:
    """Format a MUSIT person-name row for ``AgentVariant.name``.

    Uses ``Surname, Given Middle`` to match the MUSIT Det/Leg person-name column.
    """
    sur = (str(surname).strip() if surname is not None else "") or ""
    giv = (str(given).strip() if given is not None else "") or ""
    mid = (str(middle).strip() if middle is not None else "") or ""

    given_parts = " ".join(p for p in (giv, mid) if p)
    if sur and given_parts:
        name = f"{sur}, {given_parts}"
    elif sur:
        name = sur
    elif given_parts:
        name = given_parts
    else:
        return None

    if len(name) > max_len:
        name = name[:max_len]
    return name or None


def sql_alternate_person_names(schema: str) -> str:
    """All ``PERSON_NAME`` rows that are *not* the Agent primary display name.

    Agent primary comes from ``ACTORNAME`` when present, so:

    - Exclude ``PERSON_NAME`` rows whose formatted spelling matches ``ACTORNAME``.
    - When ``ACTORNAME`` is empty, exclude the fullest ``PERSON_NAME`` (Agent fallback).
    - Keep abbreviated ``VALID_PERSON_NAME`` rows as variants when Agent used ``ACTORNAME``.
    """
    sch = schema.strip().upper()
    fmt = formatted_person_name_sql("pn")
    fullest = fullest_person_name_id_sql(sch, "a")
    return f"""
        SELECT
            a.ACTOR_ID,
            pn.PERSON_NAME_ID,
            pn.PERSON_GIVEN_NAME,
            pn.PERSON_SURNAME,
            pn.PERSON_MIDDLE_NAME
        FROM {sch}.PERSON_NAME pn
        JOIN {sch}.ACTOR a
          ON a.ACTOR_ID = pn.ACTOR_ID
        WHERE (
            a.ACTORNAME IS NULL
            OR LENGTH(TRIM(a.ACTORNAME)) = 0
            OR {fmt} IS NULL
            OR UPPER({fmt}) <> UPPER(TRIM(a.ACTORNAME))
          )
          AND (
            (a.ACTORNAME IS NOT NULL AND LENGTH(TRIM(a.ACTORNAME)) > 0)
            OR pn.PERSON_NAME_ID <> {fullest}
          )
        ORDER BY a.ACTOR_ID, pn.PERSON_NAME_ID
    """


def should_skip_variant_name(
    name: str | None,
    existing_names: set[str],
) -> str | None:
    """Return a skip reason, or ``None`` if the variant should be created."""
    if not name:
        return "empty"
    if name in existing_names:
        return "duplicate"
    return None


def variant_row_from_oracle(cols: list[str], raw: tuple) -> dict[str, Any]:
    row = dict(zip(cols, raw))
    return {
        "actor_id": int(row["actor_id"]),
        "person_name_id": int(row["person_name_id"]),
        "person_given_name": row.get("person_given_name"),
        "person_surname": row.get("person_surname"),
        "person_middle_name": row.get("person_middle_name"),
    }
