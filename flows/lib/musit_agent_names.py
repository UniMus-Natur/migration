"""Choose MUSIT person names for Specify ``Agent`` (issue #74).

``ACTORNAME`` is MUSIT's display string for the actor (what the UI shows).
``VALID_PERSON_NAME_ID`` often points at an abbreviated author orthography
(e.g. ``Bl., M. N.``) and must not drive the Agent primary name.

Prefer parsed ``ACTORNAME``; fall back to a ``PERSON_NAME`` row when
``ACTORNAME`` is empty or unusable. Abbreviated spellings remain available
as ``AgentVariant``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def trunc(s: Any, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    return t if len(t) <= max_len else t[:max_len]


def parse_actorname(actorname: str | None) -> tuple[str | None, str | None, str | None]:
    """Parse MUSIT ``ACTORNAME`` (``Surname, Given Middle``) into name parts."""
    text = trunc(actorname, 512)
    if not text:
        return None, None, None
    if "," in text:
        left, right = text.split(",", 1)
        last = trunc(left, 256)
        rest = trunc(right, 200)
        if not rest:
            return None, last, None
        bits = rest.split(None, 1)
        first = trunc(bits[0], 50)
        middle = trunc(bits[1], 50) if len(bits) > 1 else None
        return first, last, middle
    return None, trunc(text, 256), None


@dataclass(frozen=True)
class AgentNameParts:
    firstname: str | None
    lastname: str
    middleinitial: str | None
    title: str | None


def choose_agent_name_parts(
    *,
    person_given_name: str | None,
    person_surname: str | None,
    person_middle_name: str | None,
    title: str | None,
    actorname: str | None,
    agent_type: int,
) -> AgentNameParts:
    """Pick Specify Agent name fields from MUSIT ``ACTORNAME`` + person-name.

    ``agent_type`` is Specify's value: 1=Person, 0=Organization.

    For persons, ``ACTORNAME`` wins whenever it yields any usable name part.
    ``title`` still comes from ``PERSON_NAME`` (``ACTORNAME`` has no title).
    """
    given = trunc(person_given_name, 50)
    surname = trunc(person_surname, 256)
    middle = trunc(person_middle_name, 50)
    title_t = trunc(title, 50)

    if agent_type == 0:
        last = surname or trunc(actorname, 256) or "(organization)"
        return AgentNameParts(
            firstname=None,
            lastname=last,
            middleinitial=None,
            title=title_t,
        )

    an_first, an_last, an_middle = parse_actorname(actorname)
    if an_first or an_last or an_middle:
        first, last, mid = an_first, an_last, an_middle
    else:
        first, last, mid = given, surname, middle

    if not (first or last):
        last = trunc(actorname, 256) or "(unknown)"

    return AgentNameParts(
        firstname=first,
        lastname=last or "(unknown)",
        middleinitial=mid,
        title=title_t,
    )


def formatted_person_name_sql(alias: str) -> str:
    """Oracle expression: ``Surname, Given Middle`` for ``PERSON_NAME`` alias."""
    sur = f"TRIM(NVL({alias}.PERSON_SURNAME, ''))"
    giv = f"TRIM(NVL({alias}.PERSON_GIVEN_NAME, ''))"
    mid = f"TRIM(NVL({alias}.PERSON_MIDDLE_NAME, ''))"
    given_parts = (
        f"TRIM(CASE "
        f"WHEN {giv} IS NOT NULL AND {mid} IS NOT NULL THEN {giv} || ' ' || {mid} "
        f"WHEN {giv} IS NOT NULL THEN {giv} "
        f"WHEN {mid} IS NOT NULL THEN {mid} "
        f"ELSE NULL END)"
    )
    return (
        f"TRIM(CASE "
        f"WHEN {sur} IS NOT NULL AND {given_parts} IS NOT NULL "
        f"THEN {sur} || ', ' || {given_parts} "
        f"WHEN {sur} IS NOT NULL THEN {sur} "
        f"WHEN {given_parts} IS NOT NULL THEN {given_parts} "
        f"ELSE NULL END)"
    )


def fullest_person_name_id_sql(schema: str, actor_alias: str = "a") -> str:
    """SQL: fullest ``PERSON_NAME_ID`` for actor ``actor_alias`` (length heuristic)."""
    sch = schema.strip().upper()
    return f"""(
            SELECT x.PERSON_NAME_ID FROM (
              SELECT pn2.PERSON_NAME_ID,
                     ROW_NUMBER() OVER (
                       ORDER BY (
                         LENGTH(NVL(pn2.PERSON_SURNAME, ''))
                         + LENGTH(NVL(pn2.PERSON_GIVEN_NAME, ''))
                         + LENGTH(NVL(pn2.PERSON_MIDDLE_NAME, ''))
                       ) DESC,
                       pn2.PERSON_NAME_ID
                     ) AS rn
                FROM {sch}.PERSON_NAME pn2
               WHERE pn2.ACTOR_ID = {actor_alias}.ACTOR_ID
            ) x
            WHERE x.rn = 1
          )"""


def preferred_person_name_id_sql(schema: str) -> str:
    """SQL: ``PERSON_NAME_ID`` for Agent fallback join (alias ``a`` = ``ACTOR``).

    Prefer the ``PERSON_NAME`` whose formatted spelling matches ``ACTORNAME``;
    otherwise the fullest row (used when ``ACTORNAME`` is empty).
    """
    sch = schema.strip().upper()
    fmt = formatted_person_name_sql("pn2")
    return f"""(
            SELECT x.PERSON_NAME_ID FROM (
              SELECT pn2.PERSON_NAME_ID,
                     ROW_NUMBER() OVER (
                       ORDER BY
                         CASE
                           WHEN a.ACTORNAME IS NOT NULL
                            AND LENGTH(TRIM(a.ACTORNAME)) > 0
                            AND {fmt} IS NOT NULL
                            AND UPPER({fmt}) = UPPER(TRIM(a.ACTORNAME))
                           THEN 0
                           ELSE 1
                         END,
                         (
                           LENGTH(NVL(pn2.PERSON_SURNAME, ''))
                           + LENGTH(NVL(pn2.PERSON_GIVEN_NAME, ''))
                           + LENGTH(NVL(pn2.PERSON_MIDDLE_NAME, ''))
                         ) DESC,
                         pn2.PERSON_NAME_ID
                     ) AS rn
                FROM {sch}.PERSON_NAME pn2
               WHERE pn2.ACTOR_ID = a.ACTOR_ID
            ) x
            WHERE x.rn = 1
          )"""
