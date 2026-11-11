"""Shared helpers for MUSIT ACTOR / PERSON_NAME → Specify Agent flows."""

from __future__ import annotations

import re

# Only schemas present in our Oracle inventory and used for specimen events.
ALLOWED_MUSIT_AGENT_SCHEMAS = frozenset({
    "MUSIT_BOTANIKK_FELLES",
    "MUSIT_ZOOLOGI_ENTOMOLOGI",
})

_ACTOR_ID_IN_REMARKS = re.compile(
    r"MUSIT-migration:\s*ACTOR;\s*schema=(?P<schema>[^;]+);\s*ACTOR_ID=(?P<actor_id>\d+)",
    re.IGNORECASE,
)

_ORACLE_NOTE_ONLY_RE = re.compile(
    r"^MUSIT-migration:\s*ACTOR;\s*(?:institution=[^;]*;\s*)?oracle_note=",
    re.IGNORECASE,
)


def musit_actor_remarks_marker(schema: str, actor_id: int) -> str:
    """Idempotency marker stored at the start of ``Agent.remarks``."""
    return f"MUSIT-migration: ACTOR; schema={schema}; ACTOR_ID={actor_id}"


def parse_actor_marker(
    remarks: str | None,
) -> tuple[str, int] | None:
    """Return ``(schema, ACTOR_ID)`` if remarks begin with a full MUSIT marker."""
    if not remarks:
        return None
    m = _ACTOR_ID_IN_REMARKS.match(str(remarks).strip())
    if m is None:
        return None
    return m.group("schema").strip().upper(), int(m.group("actor_id"))


def parse_actor_id_from_agent_remarks(remarks: str | None, schema: str) -> int | None:
    """Return ``ACTOR_ID`` if ``remarks`` is a MUSIT marker for ``schema``."""
    parsed = parse_actor_marker(remarks)
    if parsed is None:
        return None
    sch, actor_id = parsed
    if sch != schema.strip().upper():
        return None
    return actor_id


def is_oracle_note_only_remarks(remarks: str | None) -> bool:
    """True when details mangled remarks to drop ``schema`` / ``ACTOR_ID``."""
    if not remarks:
        return False
    text = str(remarks).strip()
    if parse_actor_marker(text) is not None:
        return False
    return bool(_ORACLE_NOTE_ONLY_RE.match(text))


def extract_oracle_note_from_remarks(remarks: str | None) -> str | None:
    """Return the ``oracle_note=`` payload (may contain ``;``), or None."""
    if not remarks:
        return None
    key = "oracle_note="
    idx = str(remarks).find(key)
    if idx < 0:
        return None
    note = str(remarks)[idx + len(key) :].strip()
    return note or None


def extract_institution_from_remarks(remarks: str | None) -> str | None:
    if not remarks:
        return None
    m = re.search(r"(?:^|;\s*)institution=([^;]*)", str(remarks), re.IGNORECASE)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def build_musit_actor_remarks(
    schema: str,
    actor_id: int,
    *,
    institution: str | None = None,
    oracle_note: str | None = None,
) -> str:
    """Build remarks with a full marker, optionally institution + oracle_note."""
    parts = [musit_actor_remarks_marker(schema, actor_id)]
    if institution:
        parts.append(f"institution={institution}")
    if oracle_note:
        parts.append(f"oracle_note={oracle_note}")
    return "; ".join(parts)


def find_agents_for_musit_actor(schema: str, actor_id: int):
    """Return Agents whose remarks start with the full marker (extras allowed).

    Requires Django to be set up. Does **not** find oracle_note-only orphans
    (those lost ``ACTOR_ID``); use the cleanup flow for those.
    """
    from django.db.models import Q
    from specifyweb.specify.models import Agent

    marker = musit_actor_remarks_marker(schema, actor_id)
    return list(
        Agent.objects.filter(Q(remarks=marker) | Q(remarks__startswith=marker + ";"))
        .order_by("id")
    )


def load_actor_id_to_agent_id(schema: str) -> dict[int, int]:
    """Map MUSIT ``ACTOR_ID`` → Specify ``Agent.id`` via remarks markers.

    Requires Django to be set up (``Agent`` model import).
    If duplicates exist for one ``ACTOR_ID``, keeps the lowest ``Agent.id``.
    """
    from specifyweb.specify.models import Agent

    prefix = f"MUSIT-migration: ACTOR; schema={schema};"
    out: dict[int, int] = {}
    qs = Agent.objects.filter(remarks__startswith=prefix).only("id", "remarks")
    for agent in qs.iterator(chunk_size=5000):
        actor_id = parse_actor_id_from_agent_remarks(agent.remarks, schema)
        if actor_id is None:
            continue
        agent_id = int(agent.id)
        prev = out.get(actor_id)
        if prev is None or agent_id < prev:
            out[actor_id] = agent_id
    return out
