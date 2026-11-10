"""Choose fuller MUSIT person names for Specify ``Agent`` (issue #74).

``VALID_PERSON_NAME_ID`` often points at an abbreviated orthography
(e.g. ``Bl., M. N.``). For display we prefer the longest ``PERSON_NAME``
parts, and fall back to ``ACTORNAME`` when that string is fuller.
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


def _alpha_len(s: str | None) -> int:
    if not s:
        return 0
    return sum(1 for c in s if c.isalpha())


def name_completeness_score(
    given: str | None,
    surname: str | None,
    middle: str | None = None,
) -> int:
    """Higher = more complete spelling (letter count; mild abbrev penalty)."""
    score = _alpha_len(given) + _alpha_len(surname) + _alpha_len(middle)
    sur = (surname or "").strip()
    if sur.endswith(".") and _alpha_len(sur) <= 3:
        score -= 8
    giv = (given or "").strip()
    if giv.endswith(".") and _alpha_len(giv) <= 3:
        score -= 4
    return score


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
    """Pick Specify Agent name fields from MUSIT person-name + actorname.

    ``agent_type`` is Specify's value: 1=Person, 0=Organization.
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

    pn_score = name_completeness_score(given, surname, middle)
    an_first, an_last, an_middle = parse_actorname(actorname)
    an_score = name_completeness_score(an_first, an_last, an_middle)

    if an_score > pn_score:
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


def preferred_person_name_id_sql(schema: str) -> str:
    """SQL: ``PERSON_NAME_ID`` with the fullest spelling for ``ACTOR`` alias ``a``.

    Prefers maximum length of surname+given+middle (not ``VALID_PERSON_NAME_ID``,
    which is often an abbreviation). Tie-break: lowest ``PERSON_NAME_ID``.
    """
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
               WHERE pn2.ACTOR_ID = a.ACTOR_ID
            ) x
            WHERE x.rn = 1
          )"""
