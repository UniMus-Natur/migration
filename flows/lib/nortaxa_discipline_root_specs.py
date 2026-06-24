"""Hard-coded NorTaxa root ``scientificNameId`` values per Specify ``Discipline.name``.

IDs match NorTaxa API / DwC ``taxonID`` (scientific name id). Re-verify after major NorTaxa
revisions if discipline coverage looks wrong.

Matching is **exact** on ``Discipline.name`` as stored in Specify (same strings as used when
creating disciplines). Unknown names are skipped by the flow with a log line.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NorTaxaSliceSpec:
    """How to build the ``taxonID`` keep-set from ``parentNameUsageID`` edges."""

    # NorTaxa ``taxonID`` values to include (union of subtrees rooted here).
    root_taxon_ids: tuple[str, ...]
    # Each ID here removes that node's entire subtree from the keep-set (applied after union).
    subtract_subtree_taxon_ids: tuple[str, ...] = ()


# --- UniMus:Natur discipline names → slice (v1.270 taxon.txt) ----------------------------

# Karplanter Moser: vascular land plants + mosses (Bryophyta).
#
# v1.270 ``taxon.txt`` has a valid ``Tracheophyta`` row (taxonID 205896) under Plantae, but **no
# taxon uses 205896 as ``parentNameUsageID``** — vascular checklist taxa attach under other phyla
# (Magnoliophyta, Pinophyta, Pteridophyta, etc.). Slicing on 205896 alone therefore yields an empty
# phylum in Specify while Bryophyta (1158) is populated. Union those phyla here; re-check IDs after
# a new DwC-A if coverage looks wrong.
_KARPLANTER_MOSER = NorTaxaSliceSpec(
    root_taxon_ids=(
        "1158",  # Bryophyta
        "1281",  # Magnoliophyta
        "1372",  # Pinophyta
        "1381",  # Pteridophyta
        "138365",  # Lycopodiophyta
        "138364",  # Equisetophyta
        "1256",  # Cycadophyta
        "1259",  # Ginkgophyta
        "1265",  # Gnetophyta
        "1378",  # Psilophyta
    ),
)

# Alger: chromist algae + green algae (Chlorophyta).
_ALGER = NorTaxaSliceSpec(root_taxon_ids=("819", "1191"))

# Lav Sopp: kingdom Fungi (lichens + macrofungi).
_LAV_SOPP = NorTaxaSliceSpec(root_taxon_ids=("975",))

# Insekter: class Insecta.
_INSEKTER = NorTaxaSliceSpec(root_taxon_ids=("89",))

# Marine invertebrater: Animalia minus Chordata (hard-coded anchor IDs).
_MARINE_INVERTEBRATER = NorTaxaSliceSpec(root_taxon_ids=("1",), subtract_subtree_taxon_ids=("196",))

_PATTEDYR = NorTaxaSliceSpec(root_taxon_ids=("293",))  # Mammalia
_FUGL = NorTaxaSliceSpec(root_taxon_ids=("252",))  # Aves

# Fisk og herptiler: fish (incl. jawless / cartilaginous / lobe-finned) + amphibians + reptiles.
# Under Vertebrata (127715) v1.270 also has Myxini and Holocephali as classes — include them so the
# slice is not only Actinopterygii + Elasmobranchii + lampreys (Cephalaspidomorphi).
_FISK_HERPTILER = NorTaxaSliceSpec(
    root_taxon_ids=("197", "330", "275", "277", "243", "325", "289", "323"),
)

# Paleontologi: kingdom-level coverage for fossil taxon names.
_PALEONTOLOGI = NorTaxaSliceSpec(root_taxon_ids=("1131", "819", "975", "1"))

# Geologi: no biological taxon tree for this export.
_SKIP = None

DISCIPLINE_NORTAXA_SLICE_BY_NAME: dict[str, NorTaxaSliceSpec | None] = {
    "Karplanter Moser": _KARPLANTER_MOSER,
    "Alger": _ALGER,
    "Lav Sopp": _LAV_SOPP,
    "Insekter": _INSEKTER,
    "Marine invertebrater": _MARINE_INVERTEBRATER,
    "Pattedyr": _PATTEDYR,
    "Fugl": _FUGL,
    "Fisk og herptiler": _FISK_HERPTILER,
    "Geologi": _SKIP,
    "Paleontologi": _PALEONTOLOGI,
}


def plan_nortaxa_slice(discipline_name: str | None) -> tuple[bool, str, NorTaxaSliceSpec | None]:
    """Whether to emit a TSV, skip reason (if any), and the slice spec when applicable.

    Returns ``(True, "", spec)`` to run the slice; ``(False, reason, None)`` to skip.
    """
    if not discipline_name or not str(discipline_name).strip():
        return False, "blank_discipline_name", None
    key = str(discipline_name).strip()
    if key not in DISCIPLINE_NORTAXA_SLICE_BY_NAME:
        return False, "unmapped_discipline_name", None
    spec = DISCIPLINE_NORTAXA_SLICE_BY_NAME[key]
    if spec is None:
        return False, "no_nortaxa_tree", None
    return True, "", spec
