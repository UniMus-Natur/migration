"""Load and validate structure YAML."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TreeDefSpec:
    """Geography / taxon tree defaults for a new discipline."""

    name: str = "Geography"
    preload: bool = False
    preloadfile: str | None = None


@dataclass
class CollectionSpec:
    code: str
    name: str
    catalognumberformatname: str | None = None


@dataclass
class DisciplineSpec:
    name: str
    type: str
    geography_tree: TreeDefSpec = field(default_factory=TreeDefSpec)
    taxon_tree: TreeDefSpec = field(
        default_factory=lambda: TreeDefSpec(name="Taxon", preload=False)
    )
    collections: list[CollectionSpec] = field(default_factory=list)


@dataclass
class DivisionSpec:
    name: str
    disciplines: list[DisciplineSpec] = field(default_factory=list)


@dataclass
class StructureConfig:
    institution_name: str
    divisions: list[DivisionSpec]


def _tree_from_mapping(raw: dict[str, Any] | None, *, default_name: str) -> TreeDefSpec:
    if not raw:
        return TreeDefSpec(name=default_name, preload=False)
    return TreeDefSpec(
        name=str(raw.get("name", default_name)),
        preload=bool(raw.get("preload", False)),
        preloadfile=raw.get("preloadfile") or raw.get("preloadFile"),
    )


def _collection_from_mapping(raw: dict[str, Any]) -> CollectionSpec:
    code = str(raw.get("code", "")).strip()
    name = str(raw.get("name", code)).strip()
    if not code:
        raise ValueError("collection entry requires non-empty code")
    cn = raw.get("catalognumberformatname") or raw.get("catalog_num_format_name")
    return CollectionSpec(
        code=code,
        name=name or code,
        catalognumberformatname=str(cn).strip() if cn else None,
    )


def _discipline_from_mapping(raw: dict[str, Any]) -> DisciplineSpec:
    name = str(raw.get("name", "")).strip()
    dtype = str(raw.get("type", "")).strip()
    if not name:
        raise ValueError("discipline requires non-empty name")
    if not dtype:
        raise ValueError(f"discipline {name!r} requires non-empty type")
    geo = _tree_from_mapping(raw.get("geography_tree") or raw.get("geographyTree"), default_name="Geography")
    tax = _tree_from_mapping(raw.get("taxon_tree") or raw.get("taxonTree"), default_name="Taxon")
    cols_raw = raw.get("collections") or []
    if not isinstance(cols_raw, list):
        raise ValueError(f"discipline {name!r}: collections must be a list")
    collections = [_collection_from_mapping(c) for c in cols_raw]
    return DisciplineSpec(
        name=name,
        type=dtype,
        geography_tree=geo,
        taxon_tree=tax,
        collections=collections,
    )


def _division_from_mapping(raw: dict[str, Any]) -> DivisionSpec:
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("division requires non-empty name")
    disc_raw = raw.get("disciplines") or []
    if not isinstance(disc_raw, list):
        raise ValueError(f"division {name!r}: disciplines must be a list")
    return DivisionSpec(
        name=name,
        disciplines=[_discipline_from_mapping(d) for d in disc_raw],
    )


def load_structure_config(path: str | Path) -> StructureConfig:
    """Parse structure YAML from ``path``."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"structure config not found: {p}")
    with p.open(encoding="utf-8") as fh:
        root = yaml.safe_load(fh)
    if not isinstance(root, dict):
        raise ValueError("structure YAML root must be a mapping")
    inst = str(root.get("institution_name", "")).strip()
    if not inst:
        raise ValueError("institution_name is required")
    divs_raw = root.get("divisions") or []
    if not isinstance(divs_raw, list):
        raise ValueError("divisions must be a list")
    return StructureConfig(
        institution_name=inst,
        divisions=[_division_from_mapping(d) for d in divs_raw],
    )
