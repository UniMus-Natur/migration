"""Map MUSIT classification-event fields to Specify Determination remarks."""

from __future__ import annotations

from typing import Any


def _trunc(s: Any, max_len: int) -> str | None:
    if s is None:
        return None
    t = str(s).strip()
    return (t[:max_len] if len(t) > max_len else t) or None


def determination_remarks(
    dr: dict[str, Any], *, has_resolved_determiner: bool
) -> str | None:
    """Build ``Determination.remarks`` from classification-event notes and verbatim determiner."""
    parts: list[str] = []
    event_notes = dr.get("event_notes")
    if event_notes:
        parts.append(str(event_notes).strip())
    if not has_resolved_determiner:
        det_verbatim = dr.get("det_agg_personnames") or dr.get("detname_orig")
        if det_verbatim:
            parts.append(f"Determiner (verbatim): {det_verbatim}")
    return _trunc("; ".join(parts), 4000) if parts else None
