"""One MUSIT ``ACTOR`` plus its ``PERSON_NAME`` rows, for a single-pass import.

Used by ``migrate_musit_agents_full`` so Agent name, details, and variants
come from one Oracle read instead of three flows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from flows.lib.musit_agent_variants import format_person_name_variant


@dataclass(frozen=True)
class PersonNameRow:
    person_name_id: int
    given: str | None
    surname: str | None
    middle: str | None
    title: str | None


@dataclass
class ActorBundle:
    actor_id: int
    actor_type: int | None
    actorname: str | None
    group_member_names: str | None
    birthdate: Any
    deathdate: Any
    adress: str | None
    postal_address: str | None
    email_address: str | None
    phone_number: str | None
    institution: str | None
    gender: str | None
    url: str | None
    url_note: str | None
    note: str | None
    person_names: list[PersonNameRow] = field(default_factory=list)

    def details_row(self) -> dict[str, Any]:
        return {
            "actor_id": self.actor_id,
            "actorname": self.actorname,
            "group_member_names": self.group_member_names,
            "birthdate": self.birthdate,
            "deathdate": self.deathdate,
            "adress": self.adress,
            "postal_address": self.postal_address,
            "email_address": self.email_address,
            "phone_number": self.phone_number,
            "institution": self.institution,
            "gender": self.gender,
            "url": self.url,
            "url_note": self.url_note,
            "note": self.note,
        }


def sql_actors_with_person_names(schema: str) -> str:
    """One row per ``PERSON_NAME``, actors with no names included once."""
    sch = schema.strip().upper()
    return f"""
        SELECT
            a.ACTOR_ID,
            a.ACTOR_TYPE,
            a.ACTORNAME,
            a.GROUP_MEMBER_NAMES,
            a.BIRTHDATE,
            a.DEATHDATE,
            a.ADRESS,
            a.POSTAL_ADDRESS,
            a.EMAIL_ADDRESS,
            a.PHONE_NUMBER,
            a.INSTITUTION,
            a.GENDER,
            a.URL,
            a.URL_NOTE,
            a.NOTE,
            pn.PERSON_NAME_ID,
            pn.PERSON_GIVEN_NAME,
            pn.PERSON_SURNAME,
            pn.PERSON_MIDDLE_NAME,
            pn.TITLE
        FROM {sch}.ACTOR a
        LEFT JOIN {sch}.PERSON_NAME pn
          ON pn.ACTOR_ID = a.ACTOR_ID
        ORDER BY a.ACTOR_ID, pn.PERSON_NAME_ID
    """


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def bundle_from_joined_rows(rows: list[dict[str, Any]]) -> ActorBundle:
    """Build one actor from joined Oracle rows that share ``ACTOR_ID``."""
    if not rows:
        raise ValueError("bundle_from_joined_rows requires at least one row")
    first = rows[0]
    names: list[PersonNameRow] = []
    for row in rows:
        raw_id = row.get("person_name_id")
        if raw_id is None:
            continue
        names.append(
            PersonNameRow(
                person_name_id=int(raw_id),
                given=_clean(row.get("person_given_name")),
                surname=_clean(row.get("person_surname")),
                middle=_clean(row.get("person_middle_name")),
                title=_clean(row.get("title")),
            )
        )
    actor_type = first.get("actor_type")
    return ActorBundle(
        actor_id=int(first["actor_id"]),
        actor_type=int(actor_type) if actor_type is not None else None,
        actorname=_clean(first.get("actorname")),
        group_member_names=_clean(first.get("group_member_names")),
        birthdate=first.get("birthdate"),
        deathdate=first.get("deathdate"),
        adress=_clean(first.get("adress")),
        postal_address=_clean(first.get("postal_address")),
        email_address=_clean(first.get("email_address")),
        phone_number=_clean(first.get("phone_number")),
        institution=_clean(first.get("institution")),
        gender=_clean(first.get("gender")),
        url=_clean(first.get("url")),
        url_note=_clean(first.get("url_note")),
        note=_clean(first.get("note")),
        person_names=names,
    )


def _spell_len(name: PersonNameRow) -> int:
    return len(name.surname or "") + len(name.given or "") + len(name.middle or "")


def formatted_person_name(name: PersonNameRow) -> str | None:
    return format_person_name_variant(name.surname, name.given, name.middle)


def pick_preferred_person_name(
    names: list[PersonNameRow],
    actorname: str | None,
) -> PersonNameRow | None:
    """Match ``ACTORNAME`` when possible, otherwise the fullest spelling.

    Tie-break is the lowest ``PERSON_NAME_ID``, same as the agents-flow SQL.
    """
    if not names:
        return None
    target = (actorname or "").strip().casefold()

    def sort_key(name: PersonNameRow) -> tuple:
        formatted = formatted_person_name(name) or ""
        matched = bool(target) and formatted.casefold() == target
        return (0 if matched else 1, -_spell_len(name), name.person_name_id)

    return min(names, key=sort_key)


def alternate_variant_names(
    names: list[PersonNameRow],
    actorname: str | None,
) -> list[str]:
    """Spellings that are not already the Agent primary display name.

    When ``ACTORNAME`` is set, drop rows that format to it. When it is empty,
    drop the preferred (fullest) ``PERSON_NAME`` used as the Agent name.
    """
    preferred = pick_preferred_person_name(names, actorname)
    target = (actorname or "").strip()
    target_cf = target.casefold()
    seen: set[str] = set()
    out: list[str] = []
    for name in sorted(names, key=lambda n: n.person_name_id):
        formatted = formatted_person_name(name)
        if not formatted or formatted in seen:
            continue
        if target:
            if formatted.casefold() == target_cf:
                continue
        elif preferred is not None and name.person_name_id == preferred.person_name_id:
            continue
        seen.add(formatted)
        out.append(formatted)
    return out
