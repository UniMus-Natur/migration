"""Point all UniMus:Natur biology disciplines at one canonical GeographyTreeDef.

Specify stores ``GeographyTreeDefID`` on ``Discipline``. Multiple disciplines may share the
same definition so ``Geography`` rows are reused (one admin tree for the institution's biology
collections). Geology-only disciplines stay on their own tree.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Disciplines under Biology in config/specify_structure/unimus_natur.yaml
_BIOLOGY_DISCIPLINE_NAMES: tuple[str, ...] = (
    "Karplanter Moser",
    "Alger",
    "Lav Sopp",
    "Insekter",
    "Marine invertebrater",
    "Pattedyr",
    "Fugl",
    "Fisk og herptiler",
    "Paleontologi",
)


def link_biology_disciplines_shared_geography(
    *,
    canonical_discipline_name: str = "Karplanter Moser",
    dry_run: bool = True,
) -> dict[str, Any]:
    """Set ``geographytreedef_id`` on every biology discipline to match the canonical discipline.

    Skips disciplines where ``Discipline.is_geo()`` is true (Geologi). Paleontologi is included.
    """
    from specifyweb.specify.models import Discipline

    out: dict[str, Any] = {
        "canonical_discipline_name": canonical_discipline_name,
        "canonical_treedef_id": None,
        "updated_disciplines": [],
        "skipped": [],
        "dry_run": dry_run,
    }

    canon = (
        Discipline.objects.filter(name__iexact=canonical_discipline_name.strip())
        .order_by("id")
        .first()
    )
    if canon is None:
        out["error"] = f"canonical discipline not found: {canonical_discipline_name!r}"
        return out
    tid = canon.geographytreedef_id
    if not tid:
        out["error"] = f"canonical discipline {canonical_discipline_name!r} has no geographytreedef_id"
        return out
    out["canonical_treedef_id"] = int(tid)

    for name in _BIOLOGY_DISCIPLINE_NAMES:
        d = Discipline.objects.filter(name__iexact=name.strip()).order_by("id").first()
        if d is None:
            out["skipped"].append({"discipline": name, "reason": "not_found"})
            continue
        if getattr(d, "is_geo", lambda: False)():
            out["skipped"].append({"discipline": name, "reason": "geology_discipline"})
            continue
        if int(d.geographytreedef_id or 0) == int(tid):
            out["skipped"].append({"discipline": d.name, "discipline_id": int(d.id), "reason": "already_linked"})
            continue
        if dry_run:
            out["updated_disciplines"].append(
                {"discipline": d.name, "discipline_id": int(d.id), "would_set_treedef_id": int(tid)}
            )
            continue
        Discipline.objects.filter(pk=d.id).update(geographytreedef_id=int(tid))
        out["updated_disciplines"].append({"discipline": d.name, "discipline_id": int(d.id), "treedef_id": int(tid)})
        logger.info("Linked discipline %s id=%s to GeographyTreeDefID=%s", d.name, d.id, tid)

    return out
