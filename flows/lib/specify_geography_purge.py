"""Remove ``Geography`` rows before a fresh Oracle import.

Two entry points:

- ``purge_geography_tree_for_treedef``: delete **only** rows for one ``GeographyTreeDefID``
  (safe when other disciplines keep their own trees).

- ``purge_all_geography_trees``: delete **every** ``Geography`` row in the database (all
  treedefs), then recreate a minimal **Earth** root per ``GeographyTreeDef``. Use only on
  databases where wiping *all* geography is acceptable (e.g. migration staging).

Specify blocks deleting ``Geography`` while ``Locality`` or ``Agentgeography`` still reference it.
We clear those references, then delete leaves-up until the tree is empty, then recreate Earth
roots (name ``Earth``, fullname ``Planet``) so ``load_hierarchical_geography`` can attach again.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

logger = logging.getLogger(__name__)


def _ensure_geography_earth_root(treedef_id: int, *, dry_run: bool) -> dict[str, Any]:
    """Create a single root ``Geography`` if the treedef has none (after full purge)."""
    from specifyweb.specify.models import Geography, Geographytreedefitem

    out: dict[str, Any] = {"created": False, "treedef_id": int(treedef_id)}
    if Geography.objects.filter(definition_id=treedef_id, parent_id__isnull=True).exists():
        out["message"] = "root Geography already present"
        return out

    rank_top = (
        Geographytreedefitem.objects.filter(treedef_id=treedef_id, parent_id__isnull=True)
        .order_by("rankid")
        .first()
    )
    if rank_top is None:
        out["error"] = "no top-level GeographyTreeDefItem for treedef; cannot create Earth root"
        return out
    if dry_run:
        out["message"] = "dry_run: would create Earth root Geography"
        return out

    g = Geography(
        name="Earth",
        fullname="Planet",
        definition_id=treedef_id,
        definitionitem=rank_top,
        parent=None,
        rankid=rank_top.rankid,
        isaccepted=True,
        iscurrent=True,
        guid=f"urn:migration:geography-root:treedef-{treedef_id}"[:128],
    )
    g.save()
    out["created"] = True
    out["geography_id"] = int(g.id)
    logger.info("Created Earth root Geography id=%s for GeographyTreeDefID=%s", g.id, treedef_id)
    return out


def purge_geography_tree_for_treedef(
    treedef_id: int,
    *,
    dry_run: bool,
    truncate_migration_placemap: bool = True,
) -> dict[str, Any]:
    """Delete every ``Geography`` row for ``treedef_id``, then ensure one Earth root exists.

    - Sets ``Locality.GeographyID`` to NULL where it pointed at a node in this tree.
    - Deletes ``Agentgeography`` rows for those nodes.
    - Clears ``Geography.AcceptedID`` on **any** row pointing into this tree.
    - Deletes geography nodes in waves (deepest first).
    - Optionally ``TRUNCATE`` ``migration_oracle_placemap`` (stale after a full geography wipe).
    """
    from specifyweb.specify.models import Agentgeography, Geography, Locality

    from flows.lib.migration_oracle_placemap import TABLE_NAME as PLACEMAP_TABLE

    tid = int(treedef_id)
    out: dict[str, Any] = {
        "treedef_id": tid,
        "dry_run": dry_run,
        "geography_count_before": 0,
        "localities_geography_nulled": 0,
        "agentgeography_deleted": 0,
        "geography_accepted_cleared": 0,
        "geography_deleted_total": 0,
        "placemap_truncated": False,
        "earth_root": {},
    }

    qs = Geography.objects.filter(definition_id=tid)
    out["geography_count_before"] = int(qs.count())
    if out["geography_count_before"] == 0:
        out["message"] = "no Geography rows for this treedef; nothing to purge"
        root_meta = _ensure_geography_earth_root(tid, dry_run=dry_run)
        out["earth_root"] = root_meta
        return out

    geo_id_list = list(qs.values_list("pk", flat=True))
    if dry_run:
        loc_n = Locality.objects.filter(geography_id__in=geo_id_list).count()
        ag_n = Agentgeography.objects.filter(geography_id__in=geo_id_list).count()
        out["would_null_locality_geography_rows"] = int(loc_n)
        out["would_delete_agentgeography_rows"] = int(ag_n)
        out["message"] = "dry_run: would purge Geography tree for treedef (and recreate Earth root)"
        return out

    with transaction.atomic():
        n_loc = Locality.objects.filter(geography_id__in=geo_id_list).update(geography_id=None)
        out["localities_geography_nulled"] = int(n_loc)

        ag_total, ag_detail = Agentgeography.objects.filter(geography_id__in=geo_id_list).delete()
        out["agentgeography_deleted"] = int(ag_total)

        n_acc = Geography.objects.filter(acceptedgeography_id__in=geo_id_list).update(acceptedgeography_id=None)
        out["geography_accepted_cleared"] = int(n_acc)

        total_deleted = 0
        guard = 0
        while Geography.objects.filter(definition_id=tid).exists():
            guard += 1
            if guard > 5000:
                raise RuntimeError(
                    f"purge_geography_tree_for_treedef: exceeded iteration guard (treedef_id={tid}); "
                    "possible geography cycle or unexpected FK state"
                )
            parent_ids = Geography.objects.filter(definition_id=tid).exclude(parent_id__isnull=True).values_list(
                "parent_id", flat=True
            )
            parent_set = {int(x) for x in parent_ids if x is not None}
            leaves = Geography.objects.filter(definition_id=tid).exclude(pk__in=parent_set)
            leaf_ids = list(leaves.values_list("pk", flat=True))
            if not leaf_ids:
                raise RuntimeError(
                    f"purge_geography_tree_for_treedef: no leaves found but rows remain (treedef_id={tid})"
                )
            Geography.objects.filter(pk__in=leaf_ids).delete()
            n_batch = len(leaf_ids)
            total_deleted += n_batch
            logger.info(
                "purge geography treedef_id=%s iteration=%s deleted_batch=%s total_so_far=%s",
                tid,
                guard,
                n_batch,
                total_deleted,
            )

        out["geography_deleted_total"] = int(total_deleted)

        root_meta = _ensure_geography_earth_root(tid, dry_run=False)
        if root_meta.get("error"):
            raise RuntimeError(root_meta["error"])
        out["earth_root"] = root_meta

    # TRUNCATE can implicit-commit on MySQL; run outside atomic() with the rest of the purge.
    if truncate_migration_placemap:
        from django.db import connection

        try:
            with connection.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {PLACEMAP_TABLE}")
            out["placemap_truncated"] = True
        except Exception as exc:
            logger.warning("Could not TRUNCATE %s (table may be missing): %s", PLACEMAP_TABLE, exc)

    out["message"] = "purge complete"
    return out


def purge_all_geography_trees(
    *,
    dry_run: bool,
    truncate_migration_placemap: bool = True,
) -> dict[str, Any]:
    """Delete **every** ``Geography`` row (all ``GeographyTreeDef``), then one Earth root per treedef.

    Clears **all** ``Locality.GeographyID`` that point at any geography, deletes all
    ``Agentgeography`` rows with a geography link, clears ``AcceptedGeographyID`` on geography
    nodes, then deletes the global tree in leaf batches. Finally calls
    ``_ensure_geography_earth_root`` for each ``GeographyTreeDef`` that has at least one
    ``GeographyTreeDefItem`` (skips empty defs with a warning).

    This is intentionally destructive: any collection using geography will lose its tree until
    re-imported or rebuilt.
    """
    from specifyweb.specify.models import Agentgeography, Geography, Geographytreedef, Locality

    from flows.lib.migration_oracle_placemap import TABLE_NAME as PLACEMAP_TABLE

    out: dict[str, Any] = {
        "scope": "all_treedefs",
        "dry_run": dry_run,
        "geography_count_before": 0,
        "localities_geography_nulled": 0,
        "agentgeography_deleted": 0,
        "geography_accepted_cleared": 0,
        "geography_deleted_total": 0,
        "placemap_truncated": False,
        "earth_roots": [],
        "treedefs_skipped_no_items": [],
    }

    out["geography_count_before"] = int(Geography.objects.count())
    if out["geography_count_before"] == 0:
        out["message"] = "no Geography rows in database; ensuring Earth roots only"
        out["geographytreedef_count"] = int(Geographytreedef.objects.count())
        if dry_run:
            out["message"] += " (dry_run: no deletes; would ensure Earth per treedef with items)"
            return out
        for tid in Geographytreedef.objects.order_by("id").values_list("id", flat=True):
            root_meta = _ensure_earth_for_treedef_if_items(int(tid), dry_run=False, out_list=out["earth_roots"])
            if root_meta.get("skipped"):
                out["treedefs_skipped_no_items"].append(int(tid))
            elif root_meta.get("error"):
                raise RuntimeError(f"Earth root failed for treedef_id={tid}: {root_meta['error']}")
        return out

    if dry_run:
        out["locality_with_geography"] = int(Locality.objects.exclude(geography_id=None).count())
        out["agentgeography_rows"] = int(Agentgeography.objects.exclude(geography_id=None).count())
        out["geographytreedef_count"] = int(Geographytreedef.objects.count())
        out["message"] = "dry_run: would purge ALL Geography rows and recreate Earth per treedef"
        return out

    with transaction.atomic():
        n_loc = Locality.objects.exclude(geography_id=None).update(geography_id=None)
        out["localities_geography_nulled"] = int(n_loc)

        ag_total, _ag_detail = Agentgeography.objects.exclude(geography_id=None).delete()
        out["agentgeography_deleted"] = int(ag_total)

        n_acc = Geography.objects.exclude(acceptedgeography_id=None).update(acceptedgeography_id=None)
        out["geography_accepted_cleared"] = int(n_acc)

        total_deleted = 0
        guard = 0
        while Geography.objects.exists():
            guard += 1
            if guard > 20000:
                raise RuntimeError(
                    "purge_all_geography_trees: exceeded iteration guard; possible geography cycle or FK state"
                )
            parent_ids = Geography.objects.exclude(parent_id__isnull=True).values_list("parent_id", flat=True)
            parent_set = {int(x) for x in parent_ids if x is not None}
            leaves = Geography.objects.exclude(pk__in=parent_set)
            leaf_ids = list(leaves.values_list("pk", flat=True))
            if not leaf_ids:
                raise RuntimeError("purge_all_geography_trees: no leaves found but rows remain")
            Geography.objects.filter(pk__in=leaf_ids).delete()
            total_deleted += len(leaf_ids)
            logger.info(
                "purge ALL geography iteration=%s deleted_batch=%s total_so_far=%s",
                guard,
                len(leaf_ids),
                total_deleted,
            )

        out["geography_deleted_total"] = int(total_deleted)

        for tid in Geographytreedef.objects.order_by("id").values_list("id", flat=True):
            root_meta = _ensure_earth_for_treedef_if_items(int(tid), dry_run=False, out_list=out["earth_roots"])
            if root_meta.get("skipped"):
                out["treedefs_skipped_no_items"].append(int(tid))
            elif root_meta.get("error"):
                raise RuntimeError(f"Earth root failed for treedef_id={tid}: {root_meta['error']}")

    if truncate_migration_placemap:
        from django.db import connection

        try:
            with connection.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {PLACEMAP_TABLE}")
            out["placemap_truncated"] = True
        except Exception as exc:
            logger.warning("Could not TRUNCATE %s (table may be missing): %s", PLACEMAP_TABLE, exc)

    out["message"] = "purge all geography complete"
    return out


def _ensure_earth_for_treedef_if_items(treedef_id: int, *, dry_run: bool, out_list: list[Any]) -> dict[str, Any]:
    """Ensure Earth exists for ``treedef_id`` if that def has rank items; append summary to ``out_list``."""
    from specifyweb.specify.models import Geographytreedefitem

    if not Geographytreedefitem.objects.filter(treedef_id=treedef_id).exists():
        logger.warning(
            "purge_all_geography_trees: skipping Earth for GeographyTreeDefID=%s (no GeographyTreeDefItem rows)",
            treedef_id,
        )
        return {"treedef_id": treedef_id, "skipped": True, "reason": "no_treedef_items"}

    meta = _ensure_geography_earth_root(treedef_id, dry_run=dry_run)
    entry = {"treedef_id": treedef_id, **meta}
    out_list.append(entry)
    return entry
