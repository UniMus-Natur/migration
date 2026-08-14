"""Map MUSIT typification type vocabulary to Specify Determination.typeStatusName."""

from __future__ import annotations

from typing import Any

# MUSIT TYPES.TYPETERM uses hyphens; Specify's TypeStatus pick list does not.
_TYPE_STATUS_ALIASES = {
    "ex-holotype": "Exholotype",
    "ex-type": "Extype",
    "ex-isotype": "Exisotype",
}


def resolve_type_status_name(raw: Any) -> str | None:
    """Return a Specify ``typeStatusName`` value from MUSIT ``TYPES.TYPETERM``.

    ``TYPIFICATION_EVENT.TYPE_STATUS`` is a free-text leftover and is often null;
    the MUSIT UI stores the chosen status on ``TYPIFICATION_TYPE_ID``.
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    mapped = _TYPE_STATUS_ALIASES.get(value.lower(), value)
    return mapped[:50] or None
