"""Pure taxon name matching helpers for MUSIT → Specify determination linking."""

from __future__ import annotations

import re
from typing import Any


def norm_taxon_label(s: Any) -> str:
    if s is None:
        return ""
    v = re.sub(r"[^0-9A-Za-z]+", " ", str(s).strip().lower())
    return " ".join(v.split())


def taxon_label_candidates(taxon: Any) -> list[str]:
    """Normalized labels for a Specify taxon (incl. ``name`` + ``author``)."""
    name = getattr(taxon, "name", None)
    author = getattr(taxon, "author", None)
    combined = " ".join(part for part in (name, author) if part)
    seen: set[str] = set()
    out: list[str] = []
    for raw in (
        name,
        getattr(taxon, "fullname", None),
        getattr(taxon, "fullnamewithauthor", None),
        combined or None,
    ):
        label = norm_taxon_label(raw)
        if label and label not in seen:
            seen.add(label)
            out.append(label)
    return out


def taxon_matches_valid_classterm(taxon: Any, valid_classterm: str | None) -> bool:
    """Guardrail: ID matches must not contradict the determination text."""
    probe = norm_taxon_label(valid_classterm)
    if not probe:
        return True

    names = taxon_label_candidates(taxon)
    if not names:
        return True

    probe_tokens = probe.split()
    for cand in names:
        if cand == probe:
            return True
        if len(probe_tokens) >= 2 and (probe.startswith(cand) or cand.startswith(probe)):
            return True
    return False


def binomial_prefix_from_valid_classterm(valid_classterm: str | None) -> str | None:
    """Genus + epithet when ``valid_classterm`` has trailing authorship (3+ tokens)."""
    probe = " ".join((valid_classterm or "").strip().split())
    tokens = probe.split()
    if len(tokens) < 3:
        return None
    return " ".join(tokens[:2])
