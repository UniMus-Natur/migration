"""Helpers for MUSIT ``PERSON_NAME`` → Specify ``AgentVariant`` fill-in migration.

The Phase 1.1 agents flow stores only the preferred (or fallback) name on ``Agent``.
Alternate ``PERSON_NAME`` rows are loaded later as ``AgentVariant`` (varType Variant=0)
so synonymic / label orthographies are not lost.
"""

from __future__ import annotations

from typing import Any

# Specify default picklist: Variant=0, Vernacular=1, Author=2, Author Abbrev.=3, Label Name=4
AGENT_VARIANT_VARTYPE_VARIANT = 0

# AgentVariant.name max length in Specify
_AGENT_VARIANT_NAME_MAX = 255


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


def preferred_person_name_id_sql(schema: str) -> str:
    """SQL expression: preferred ``PERSON_NAME_ID`` for an ``ACTOR`` alias ``a``."""
    sch = schema.strip().upper()
    return f"""NVL(
            a.VALID_PERSON_NAME_ID,
            (SELECT MIN(pn2.PERSON_NAME_ID)
               FROM {sch}.PERSON_NAME pn2
              WHERE pn2.ACTOR_ID = a.ACTOR_ID)
          )"""


def sql_alternate_person_names(schema: str) -> str:
    """All ``PERSON_NAME`` rows that are *not* the preferred/fallback name on Agent."""
    sch = schema.strip().upper()
    pref = preferred_person_name_id_sql(sch)
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
        WHERE pn.PERSON_NAME_ID <> {pref}
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
