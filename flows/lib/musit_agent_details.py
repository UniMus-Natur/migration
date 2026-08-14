"""Parse and map MUSIT ``ACTOR`` detail fields onto existing Specify Agents.

Used by the Phase 1.1c fill-in flow (does not create Agents).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# Specify date precision: 1=day, 2=month, 3=year (common convention).
DATE_PRECISION_DAY = 1
DATE_PRECISION_MONTH = 2
DATE_PRECISION_YEAR = 3

_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_]*)>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_WIKIDATA_RE = re.compile(
    r"(?:https?://(?:www\.)?wikidata\.org/wiki/)?(Q\d+)\b",
    re.IGNORECASE,
)
_ORCID_RE = re.compile(
    r"(?:https?://(?:www\.)?orcid\.org/)?(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_PARTIAL_DATE_RE = re.compile(
    r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?\s*$"
)


def trunc(s: Any, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    return t if len(t) <= max_len else t[:max_len]


def coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def parse_partial_date(text: str | None) -> tuple[date | None, int | None]:
    """Parse ``YYYY``, ``YYYY-MM``, or ``YYYY-MM-DD`` → (date, precision)."""
    if text is None:
        return None, None
    m = _PARTIAL_DATE_RE.match(str(text).strip())
    if m is None:
        return None, None
    year = int(m.group(1))
    month_s, day_s = m.group(2), m.group(3)
    try:
        if month_s is None:
            return date(year, 1, 1), DATE_PRECISION_YEAR
        month = int(month_s)
        if day_s is None:
            return date(year, month, 1), DATE_PRECISION_MONTH
        return date(year, month, int(day_s)), DATE_PRECISION_DAY
    except ValueError:
        return None, None


def dates_from_actor_note(note: str | None) -> tuple[
    tuple[date | None, int | None],
    tuple[date | None, int | None],
]:
    """Extract ``<FODT>`` / ``<DOD>`` (birth / death) from MUSIT ``ACTOR.NOTE``."""
    birth: tuple[date | None, int | None] = (None, None)
    death: tuple[date | None, int | None] = (None, None)
    if not note:
        return birth, death
    for tag, body in _TAG_RE.findall(str(note)):
        upper = tag.upper()
        parsed = parse_partial_date(body)
        if upper == "FODT" and parsed[0] is not None:
            birth = parsed
        elif upper == "DOD" and parsed[0] is not None:
            death = parsed
    return birth, death


@dataclass(frozen=True)
class ParsedIdentifier:
    identifier_type: str
    identifier: str


def parse_url_note_identifiers(url_note: str | None) -> list[ParsedIdentifier]:
    """Extract Wikidata / ORCID / generic URL identifiers from ``URL_NOTE``."""
    if not url_note:
        return []
    text = str(url_note).strip()
    if not text:
        return []

    found: list[ParsedIdentifier] = []
    seen: set[tuple[str, str]] = set()

    def add(itype: str, ident: str) -> None:
        ident = ident.strip()
        if not ident:
            return
        key = (itype, ident)
        if key in seen:
            return
        seen.add(key)
        found.append(ParsedIdentifier(identifier_type=itype, identifier=ident[:2048]))

    # Tagged bodies first (e.g. <personID>https://www.wikidata.org/wiki/Q15</personID>).
    for tag, body in _TAG_RE.findall(text):
        body = body.strip()
        tag_u = tag.upper()
        wd = _WIKIDATA_RE.search(body)
        if wd:
            add("Wikidata", wd.group(1).upper())
            continue
        oc = _ORCID_RE.search(body)
        if oc:
            add("ORCID", oc.group(1))
            continue
        if "ORCID" in tag_u:
            oc2 = _ORCID_RE.search(body) or re.search(
                r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", body, re.I
            )
            if oc2:
                add("ORCID", oc2.group(1))
                continue
        if "WIKIDATA" in tag_u or "PERSONID" in tag_u:
            wd2 = _WIKIDATA_RE.search(body) or re.search(r"(Q\d+)", body, re.I)
            if wd2:
                add("Wikidata", wd2.group(1).upper())
                continue
        if body.lower().startswith("http"):
            add("URL", body)

    # Untagged free text.
    for m in _WIKIDATA_RE.finditer(text):
        add("Wikidata", m.group(1).upper())
    for m in _ORCID_RE.finditer(text):
        add("ORCID", m.group(1))
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(").,;")
        if "wikidata.org" in url.lower() or "orcid.org" in url.lower():
            continue
        add("URL", url)

    return found


def sql_actor_details(schema: str) -> str:
    sch = schema.strip().upper()
    return f"""
        SELECT
            a.ACTOR_ID,
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
            a.NOTE
        FROM {sch}.ACTOR a
        ORDER BY a.ACTOR_ID
    """


def actor_details_row_from_oracle(cols: list[str], raw: tuple) -> dict[str, Any]:
    row = dict(zip(cols, raw))
    return {
        "actor_id": int(row["actor_id"]),
        "actorname": row.get("actorname"),
        "group_member_names": row.get("group_member_names"),
        "birthdate": row.get("birthdate"),
        "deathdate": row.get("deathdate"),
        "adress": row.get("adress"),
        "postal_address": row.get("postal_address"),
        "email_address": row.get("email_address"),
        "phone_number": row.get("phone_number"),
        "institution": row.get("institution"),
        "gender": row.get("gender"),
        "url": row.get("url"),
        "url_note": row.get("url_note"),
        "note": row.get("note"),
    }


@dataclass
class AgentDetailsPatch:
    """Field updates / related creates derived from one Oracle ACTOR row."""

    agent_fields: dict[str, Any] = field(default_factory=dict)
    create_address: bool = False
    address_fields: dict[str, Any] = field(default_factory=dict)
    identifiers: list[ParsedIdentifier] = field(default_factory=list)
    remarks: str | None = None


def build_agent_details_patch(
    row: dict[str, Any],
    *,
    agent_email: str | None,
    agent_url: str | None,
    agent_dob: date | None,
    agent_dod: date | None,
    agent_text1: str | None,
    agent_text2: str | None,
    agent_text3: str | None,
    agent_text4: str | None,
    agent_text5: str | None,
    agent_remarks: str | None,
    has_address: bool,
    existing_identifier_keys: set[tuple[str, str]],
) -> AgentDetailsPatch:
    """Build idempotent updates: only fill empty Agent fields / missing related rows."""
    patch = AgentDetailsPatch()
    fields: dict[str, Any] = {}

    display = trunc(row.get("actorname"), 65535)
    if display and not (agent_text1 or "").strip():
        fields["text1"] = display

    group_name = trunc(row.get("group_member_names"), 65535)
    if group_name and not (agent_text2 or "").strip():
        fields["text2"] = group_name

    gender = trunc(row.get("gender"), 65535)
    if gender and not (agent_text3 or "").strip():
        fields["text3"] = gender

    institution = trunc(row.get("institution"), 65535)
    if institution and not (agent_text4 or "").strip():
        fields["text4"] = institution

    url_note = trunc(row.get("url_note"), 65535)
    if url_note and not (agent_text5 or "").strip():
        fields["text5"] = url_note

    email = trunc(row.get("email_address"), 50)
    if email and not (agent_email or "").strip():
        fields["email"] = email

    url = trunc(row.get("url"), 1024)
    if url and not (agent_url or "").strip():
        fields["url"] = url

    col_dob = coerce_date(row.get("birthdate"))
    col_dod = coerce_date(row.get("deathdate"))
    note_dob, note_dod = dates_from_actor_note(row.get("note"))

    if agent_dob is None:
        if col_dob is not None:
            fields["dateofbirth"] = col_dob
            fields["dateofbirthprecision"] = DATE_PRECISION_DAY
        elif note_dob[0] is not None:
            fields["dateofbirth"] = note_dob[0]
            fields["dateofbirthprecision"] = note_dob[1]

    if agent_dod is None:
        if col_dod is not None:
            fields["dateofdeath"] = col_dod
            fields["dateofdeathprecision"] = DATE_PRECISION_DAY
        elif note_dod[0] is not None:
            fields["dateofdeath"] = note_dod[0]
            fields["dateofdeathprecision"] = note_dod[1]

    note = trunc(row.get("note"), 65000)
    remarks = agent_remarks or ""
    if note:
        # Preserve migration marker; replace/append full oracle_note= payload.
        if remarks.upper().startswith("MUSIT-MIGRATION:"):
            first = remarks.split(";", 1)[0].strip()
            extras: list[str] = [first]
            if institution:
                extras.append(f"institution={trunc(institution, 500)}")
            extras.append(f"oracle_note={note}")
            new_remarks = "; ".join(extras)
            if new_remarks != remarks:
                patch.remarks = new_remarks
        elif "oracle_note=" not in remarks:
            patch.remarks = (remarks + "; " if remarks else "") + f"oracle_note={note}"

    patch.agent_fields = fields

    addr = trunc(row.get("adress"), 255)
    postal = trunc(row.get("postal_address"), 255)
    phone = trunc(row.get("phone_number"), 50)
    if not has_address and (addr or postal or phone):
        patch.create_address = True
        af: dict[str, Any] = {"isprimary": True, "iscurrent": True}
        if addr:
            af["address"] = addr
        if postal:
            af["address2"] = postal
        if phone:
            af["phone1"] = phone
        patch.address_fields = af

    for ident in parse_url_note_identifiers(row.get("url_note")):
        key = (ident.identifier_type, ident.identifier)
        if key not in existing_identifier_keys:
            patch.identifiers.append(ident)

    return patch


def patch_is_noop(patch: AgentDetailsPatch) -> bool:
    return (
        not patch.agent_fields
        and patch.remarks is None
        and not patch.create_address
        and not patch.identifiers
    )
