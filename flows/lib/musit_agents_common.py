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


def musit_actor_remarks_marker(schema: str, actor_id: int) -> str:
    """Idempotency marker stored at the start of ``Agent.remarks``."""
    return f"MUSIT-migration: ACTOR; schema={schema}; ACTOR_ID={actor_id}"


def parse_actor_id_from_agent_remarks(remarks: str | None, schema: str) -> int | None:
    """Return ``ACTOR_ID`` if ``remarks`` is a MUSIT marker for ``schema``."""
    if not remarks:
        return None
    m = _ACTOR_ID_IN_REMARKS.match(str(remarks).strip())
    if m is None:
        return None
    if m.group("schema").strip().upper() != schema.strip().upper():
        return None
    return int(m.group("actor_id"))
