"""Load Oracle MUSIT hierarchical places into Specify ``Geography`` and ``Locality`` (Django ORM)."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)


def _progress_log(msg: str, *args: Any) -> None:
    """Prefer Prefect run logger so messages show on the flow run; fall back to std logging."""
    try:
        from prefect import get_run_logger

        get_run_logger().info(msg, *args)
    except Exception:
        logger.info(msg, *args)


class OracleGeographyMigrationError(RuntimeError):
    """First blocking geography/locality/placemap failure; ``context`` is JSON-friendly for reports."""

    def __init__(self, message: str, *, context: dict[str, Any]):
        self.context = context
        super().__init__(message)


def _fail_fast(
    phase: str,
    summary: str,
    *,
    cause: BaseException | None = None,
    **context: Any,
) -> None:
    """Log full context at CRITICAL and abort the run (Prefect should mark failed)."""
    safe: dict[str, Any] = {"phase": phase, "summary": summary}
    for k, v in context.items():
        try:
            json.dumps(v)
            safe[k] = v
        except (TypeError, ValueError):
            safe[k] = repr(v)
    blob = json.dumps(safe, indent=2, default=str)[:12000]
    logger.critical("oracle_geography FAIL_FAST %s\n%s", phase, blob)
    try:
        from prefect import get_run_logger

        get_run_logger().critical("FAIL_FAST %s\n%s", phase, blob[:8000])
    except Exception:
        pass
    msg = f"[{phase}] {summary}\n{blob[:4000]}"
    err = OracleGeographyMigrationError(msg, context=safe)
    if cause is not None:
        raise err from cause
    raise err


def _format_duration(seconds: float) -> str:
    if seconds != seconds or seconds < 0:  # NaN
        return "?"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60}s"
    return f"{s // 3600}h{(s % 3600) // 60}m"


def _eta_remaining(elapsed_s: float, done: int, total: int) -> str:
    if done <= 0 or total <= done or elapsed_s <= 0:
        return "…"
    rate = elapsed_s / done
    return _format_duration(rate * (total - done))


def _norm_type(s: str | None) -> str:
    return (s or "").strip().lower()


def oracle_type_name_to_rank_item_name(type_name: str | None) -> str:
    """Map MUSIT ``TYPES`` label to a logical rank name (English).

    Empty / unknown Oracle type returns ``""`` so the loader can infer rank from the
    parent's ``rankid`` (see ``rank_for_row``): defaulting everything to County breaks
    world→continent chains when types are NULL.
    """
    t = _norm_type(type_name)
    if not t:
        return ""
    if "kommune" in t or "kommun" in t:
        return "Municipality"
    if "fylke" in t:
        return "County"
    if "land" in t and "fylke" not in t:
        return "Country"
    if "kontinent" in t or "continent" in t:
        return "Continent"
    if "region" in t or "del" in t:
        return "State"
    return "County"


# English logical names from ``oracle_type_name_to_rank_item_name`` → try these keys on
# ``GeographyTreeDefItem.Name`` (lowercased). Norwegian / mixed Specify trees use local names.
_RANK_SYNONYMS: dict[str, tuple[str, ...]] = {
    "continent": ("continent", "kontinent", "verdensdel"),
    "country": ("country", "land", "nation"),
    "state": ("state", "province", "region", "del"),
    "county": ("county", "fylke", "kommuneregion", "region"),
    "municipality": ("municipality", "kommune", "kommun", "kommune/region"),
}


def _resolve_rank_item(rank_items: dict[str, Any], logical_name: str) -> Any:
    """Map logical rank (English) to a ``GeographyTreeDefItem`` instance for this treedef."""
    key = (logical_name or "").strip().lower()
    if not key:
        return None
    candidates: tuple[str, ...] = (key,)
    if key in _RANK_SYNONYMS:
        candidates = candidates + _RANK_SYNONYMS[key]
    for cand in candidates:
        it = rank_items.get(cand)
        if it is not None:
            return it
    for cand in _RANK_SYNONYMS.get("county", ()):
        it = rank_items.get(cand)
        if it is not None:
            return it
    return None


def _treedef_items_ordered_by_rank(treedef_id: int) -> list[Any]:
    """All ``GeographyTreeDefItem`` rows for this treedef, shallow-to-deep by ``rankid``."""
    from specifyweb.specify.models import Geographytreedefitem

    return list(
        Geographytreedefitem.objects.filter(treedef_id=treedef_id).order_by("rankid", "id")
    )


# Oracle often has a synthetic ``WORLD`` (or similar) under Earth; with NULL ``TYPES`` every
# child used to pick the first rank deeper than Earth → Continent for WORLD, then Country for
# the next row. Climbing past these for **untyped** rows keeps continent-level buckets correct.
_NULL_ORACLE_TYPE_REDUNDANT_PARENT_NAMES: frozenset[str] = frozenset(
    {
        "world",
        "the world",
        "verden",
        "whole world",
        "global",
    }
)


def _effective_parent_geography_for_untyped(parent_geo: Any, r: HierRow, earth: Any) -> Any:
    """When Oracle ``TYPES`` is NULL, skip placeholder parents so rank + tree match real geography."""
    if _norm_type(r.type_name):
        return parent_geo
    guard = 0
    while (
        parent_geo is not None
        and earth is not None
        and int(getattr(parent_geo, "id", 0)) != int(getattr(earth, "id", -1))
        and guard < 24
    ):
        guard += 1
        pname = (getattr(parent_geo, "name", None) or "").strip().lower()
        if pname in _NULL_ORACLE_TYPE_REDUNDANT_PARENT_NAMES:
            nxt = getattr(parent_geo, "parent", None)
            if nxt is None:
                break
            parent_geo = nxt
            continue
        break
    return parent_geo


def ensure_municipality_rank(treedef_id: int, *, dry_run: bool) -> dict[str, Any]:
    """Add a ``Municipality`` rank under ``County`` when missing (Norwegian kommune level)."""
    from specifyweb.specify.models import Geographytreedefitem

    out: dict[str, Any] = {"added": False, "treedef_id": treedef_id, "dry_run": dry_run}
    county = (
        Geographytreedefitem.objects.filter(treedef_id=treedef_id)
        .filter(name__iexact="County")
        .order_by("rankid")
        .first()
    )
    if county is None:
        county = (
            Geographytreedefitem.objects.filter(treedef_id=treedef_id)
            .filter(name__iexact="Fylke")
            .order_by("rankid")
            .first()
        )
    if county is None:
        out["error"] = "no County/Fylke rank on geography treedef"
        return out
    exists = Geographytreedefitem.objects.filter(treedef_id=treedef_id, name__iexact="Municipality").exists()
    if exists:
        return out
    if dry_run:
        out["would_add"] = "Municipality"
        return out
    Geographytreedefitem.objects.create(
        treedef_id=treedef_id,
        name="Municipality",
        title="Municipality",
        rankid=500,
        isenforced=True,
        isinfullname=True,
        parent=county,
    )
    out["added"] = True
    logger.info("Created Municipality rank under GeographyTreeDefID=%s", treedef_id)
    return out


def ensure_settlement_rank(treedef_id: int, *, dry_run: bool) -> dict[str, Any]:
    """Add ``Settlement`` below the deepest rank (usually Municipality) for sub-kommune Oracle nodes.

    Specify requires each child geography to have a strictly greater ``rankid`` than its parent.
    When the parent is already at Municipality (500), there must be a deeper treedef item.
    """
    from specifyweb.specify.models import Geographytreedefitem

    out: dict[str, Any] = {"added": False, "treedef_id": treedef_id, "dry_run": dry_run}
    if Geographytreedefitem.objects.filter(treedef_id=treedef_id, name__iexact="Settlement").exists():
        return out
    muni = (
        Geographytreedefitem.objects.filter(treedef_id=treedef_id)
        .filter(name__iexact="Municipality")
        .order_by("-rankid")
        .first()
    )
    parent_item = muni
    if parent_item is None:
        parent_item = Geographytreedefitem.objects.filter(treedef_id=treedef_id).order_by("-rankid", "-id").first()
    if parent_item is None:
        out["error"] = "no geography treedef items"
        return out
    occupied = set(
        int(x) for x in Geographytreedefitem.objects.filter(treedef_id=treedef_id).values_list("rankid", flat=True)
    )
    new_rank = int(parent_item.rankid) + 100
    while new_rank in occupied:
        new_rank += 10
    if dry_run:
        out["would_add"] = "Settlement"
        out["would_rankid"] = new_rank
        return out
    Geographytreedefitem.objects.create(
        treedef_id=treedef_id,
        name="Settlement",
        title="Settlement",
        rankid=new_rank,
        isenforced=True,
        isinfullname=True,
        parent=parent_item,
    )
    out["added"] = True
    logger.info(
        "Created Settlement rank (rankid=%s) under parent=%s for GeographyTreeDefID=%s",
        new_rank,
        getattr(parent_item, "name", None),
        treedef_id,
    )
    return out


def _rank_items_by_name_lower(treedef_id: int) -> dict[str, Any]:
    from specifyweb.specify.models import Geographytreedefitem

    m: dict[str, Any] = {}
    for it in Geographytreedefitem.objects.filter(treedef_id=treedef_id).order_by("rankid"):
        key = (it.name or "").strip().lower()
        if key and key not in m:
            m[key] = it
    return m


@dataclass
class HierRow:
    hierarch_place_id: int
    placename: str
    place_id_partof: int | None
    type_name: str | None


# MUSIT ``TYPES`` human-readable label column differs between deployments (not always ``NAME``).
_TYPES_LABEL_COLUMN_PRIORITY = (
    "TYPE_NAME",
    "TYPENAME",
    "TYPELABEL",
    "LABEL",
    "DESCRIPTION",
    "TEXT",
    "TYPE_TEXT",
    "CODE",
    "VALUE",
    "NAME",
)


def _types_label_column_name(cur: Any, owner: str) -> str | None:
    """Return first matching ``TYPES`` column name for hierarchy type labels, or ``None``."""
    o = owner.upper()
    cur.execute(
        """
        SELECT column_name FROM all_tab_columns
        WHERE owner = :owner AND table_name = 'TYPES'
        """,
        {"owner": o},
    )
    existing = {str(r[0]).upper() for r in cur.fetchall()}
    for cand in _TYPES_LABEL_COLUMN_PRIORITY:
        if cand in existing:
            return cand
    return None


@dataclass
class GeographyLoadStats:
    treedef_id: int
    owner: str
    rows_read: int = 0
    geographies_created: int = 0
    geographies_skipped_existing: int = 0
    errors: list[str] = field(default_factory=list)


def _fetch_hierarchical_rows(cur: Any, owner: str) -> list[HierRow]:
    o = owner.upper()
    label_col = _types_label_column_name(cur, owner)
    if label_col:
        _progress_log("oracle_geography | Oracle fetch | %s.TYPES label column: %s", o, label_col)
    else:
        logger.warning(
            "Oracle geography: no known label column on %s.TYPES; using default rank mapping for all nodes",
            o,
        )
    type_expr = f"t.{label_col} AS TYPE_NAME" if label_col else "CAST(NULL AS VARCHAR2(4000)) AS TYPE_NAME"
    sql = f"""
    SELECT h.HIERARCH_PLACE_ID, h.HIERACHICAL_PLACENAME, h.PLACE_ID_PARTOF, {type_expr}
      FROM {o}.hierarchical_place_old h
      LEFT JOIN {o}.types t ON t.TYPE_ID = h.HIERACHICAL_TYPE
    """
    t0 = time.perf_counter()
    cur.execute(sql)
    rows: list[HierRow] = []
    for r in cur.fetchall():
        hid = int(r[0])
        name = (r[1] or "").strip() or f"ID_{hid}"
        partof = int(r[2]) if r[2] is not None else None
        tname = r[3]
        rows.append(HierRow(hid, name, partof, tname))
    _progress_log(
        "oracle_geography | Oracle fetch | %s hierarchical_place_old rows=%s elapsed=%s",
        o,
        len(rows),
        _format_duration(time.perf_counter() - t0),
    )
    return rows


def _toposort_hierarchical(rows: list[HierRow]) -> list[HierRow]:
    """Parents before children; ``HierRow`` is not hashable so we track ``hierarch_place_id`` in sets."""
    by_id = {r.hierarch_place_id: r for r in rows}
    ids = set(by_id)

    def deps(r: HierRow) -> set[int]:
        if r.place_id_partof is None or r.place_id_partof not in ids:
            return set()
        return {r.place_id_partof}

    ordered: list[HierRow] = []
    ordered_ids: set[int] = set()
    remaining_ids = set(by_id.keys())
    guard = 0
    while remaining_ids and guard < len(rows) + 5:
        guard += 1
        progressed = False
        for hid in list(remaining_ids):
            r = by_id[hid]
            if deps(r).issubset(ordered_ids):
                ordered.append(r)
                ordered_ids.add(hid)
                remaining_ids.remove(hid)
                progressed = True
        if not progressed:
            for hid in list(remaining_ids):
                ordered.append(by_id[hid])
                ordered_ids.add(hid)
                remaining_ids.remove(hid)
    return ordered


def load_hierarchical_geography(
    *,
    oracle_cursor: Any,
    owner: str,
    treedef_id: int,
    dry_run: bool,
) -> tuple[GeographyLoadStats, dict[int, int]]:
    """Insert ``Geography`` rows for ``HIERARCHICAL_PLACE_OLD``; return stats and Oracle→Specify id map."""
    from specifyweb.specify.models import Geography

    close_old_connections()
    stats = GeographyLoadStats(treedef_id=treedef_id, owner=owner)
    oracle_to_geo: dict[int, int] = {}

    t_load = time.perf_counter()
    rows = _fetch_hierarchical_rows(oracle_cursor, owner)
    stats.rows_read = len(rows)
    guid_prefix = f"urn:oracle:{owner.lower()}:hpo:"
    t_pf = time.perf_counter()
    existing_guid_to_id: dict[str, int] = dict(
        Geography.objects.filter(definition_id=treedef_id, guid__startswith=guid_prefix).values_list("guid", "id")
    )
    _progress_log(
        "oracle_geography | geography | owner=%s | rows=%s existing_guids=%s prefetch=%s total=%s",
        owner,
        len(rows),
        len(existing_guid_to_id),
        _format_duration(time.perf_counter() - t_pf),
        _format_duration(time.perf_counter() - t_load),
    )
    geo_by_pk: dict[int, Any] = {}
    rank_items = _rank_items_by_name_lower(treedef_id)
    if "municipality" not in rank_items and "kommune" not in rank_items:
        mr = ensure_municipality_rank(treedef_id, dry_run=dry_run)
        if mr.get("error"):
            _fail_fast(
                "geography.ensure_municipality_rank",
                str(mr["error"]),
                owner=owner,
                treedef_id=treedef_id,
                dry_run=dry_run,
                ensure_municipality_rank=mr,
            )
        rank_items = _rank_items_by_name_lower(treedef_id)

    sr = ensure_settlement_rank(treedef_id, dry_run=dry_run)
    if sr.get("error"):
        _fail_fast(
            "geography.ensure_settlement_rank",
            str(sr["error"]),
            owner=owner,
            treedef_id=treedef_id,
            dry_run=dry_run,
            ensure_settlement_rank=sr,
        )
    if sr.get("added"):
        rank_items = _rank_items_by_name_lower(treedef_id)

    ordered_def_items = _treedef_items_ordered_by_rank(treedef_id)
    _rk = sorted(rank_items.keys())
    _rk_suffix = f" (+{len(_rk) - 60} more)" if len(_rk) > 60 else ""
    _progress_log(
        "oracle_geography | geography | owner=%s | treedef rank keys (lowercase names, first 60): %s%s",
        owner,
        _rk[:60],
        _rk_suffix,
    )

    earth = Geography.objects.filter(definition_id=treedef_id, parent_id__isnull=True).order_by("id").first()
    if earth is None:
        _fail_fast(
            "geography.no_earth_root",
            "no root Geography (parent_id IS NULL) for this treedef — cannot attach tree",
            owner=owner,
            treedef_id=treedef_id,
        )
    geo_by_pk[int(earth.id)] = earth

    def _geo_model(pk: int) -> Any:
        if pk not in geo_by_pk:
            g = Geography.objects.filter(pk=pk).first()
            if g is not None:
                geo_by_pk[pk] = g
        return geo_by_pk.get(pk)

    def rank_for_row(r: HierRow, parent_geo: Any) -> Any:
        """Specify requires child ``rankid`` > parent ``rankid`` on Geography trees."""
        pr = getattr(parent_geo, "rankid", None)
        parent_rid = int(pr) if pr is not None else -1
        nm = oracle_type_name_to_rank_item_name(r.type_name)
        di = _resolve_rank_item(rank_items, nm) if nm else None
        if di is not None and int(di.rankid) > parent_rid:
            return di
        for it in ordered_def_items:
            if int(it.rankid) > parent_rid:
                return it
        return None

    ordered_rows = _toposort_hierarchical(rows)
    total_g = len(ordered_rows)
    _progress_log(
        "oracle_geography | geography | owner=%s | toposort done n=%s dry_run=%s — starting Specify Geography writes",
        owner,
        total_g,
        dry_run,
    )
    if total_g == 0:
        _progress_log("oracle_geography | geography | owner=%s | nothing to insert", owner)
    t_loop = time.perf_counter()
    for i, r in enumerate(ordered_rows, start=1):
        guid = f"urn:oracle:{owner.lower()}:hpo:{r.hierarch_place_id}"
        if len(guid) > 128:
            guid = guid[:128]
        ex_id = existing_guid_to_id.get(guid)
        if ex_id is not None:
            oracle_to_geo[r.hierarch_place_id] = ex_id
            if ex_id not in geo_by_pk:
                g = Geography.objects.filter(pk=ex_id).first()
                if g is not None:
                    geo_by_pk[ex_id] = g
            stats.geographies_skipped_existing += 1
            continue
        parent_geo = earth
        if r.place_id_partof is not None and r.place_id_partof in oracle_to_geo:
            pid = oracle_to_geo[r.place_id_partof]
            parent_geo = _geo_model(pid) or earth
        parent_geo = _effective_parent_geography_for_untyped(parent_geo, r, earth)
        di = rank_for_row(r, parent_geo)
        if di is None:
            nm = oracle_type_name_to_rank_item_name(r.type_name)
            pr = getattr(parent_geo, "rankid", None)
            parent_rid = int(pr) if pr is not None else -1
            _fail_fast(
                "geography.no_rank_item",
                "No GeographyTreeDefItem with rankid greater than parent (treedef exhausted?)",
                owner=owner,
                treedef_id=treedef_id,
                hierarch_place_id=r.hierarch_place_id,
                placename=r.placename,
                place_id_partof=r.place_id_partof,
                oracle_type_name_raw=r.type_name,
                logical_rank=nm or None,
                parent_geography_id=int(parent_geo.id),
                parent_rankid=parent_rid,
                rank_keys_sorted=_rk,
                treedef_rankids_ordered=[int(x.rankid) for x in ordered_def_items],
                loop_index=i,
                total_geography=total_g,
                guid=guid,
            )
        name = r.placename[:128] if len(r.placename) > 128 else r.placename
        fullname = f"{parent_geo.fullname or parent_geo.name}, {name}"[:500]
        if dry_run:
            stats.geographies_created += 1
            continue
        try:
            g = Geography(
                name=name,
                fullname=fullname,
                definition_id=treedef_id,
                definitionitem=di,
                parent=parent_geo,
                rankid=di.rankid,
                isaccepted=True,
                iscurrent=True,
                guid=guid,
            )
            # Specify ``Geography`` uses ``Tree.save()``: it ``select_for_update()``s the parent row.
            # That must run inside a DB transaction (Django API). One short ``atomic()`` per insert
            # keeps Prefect workers valid without raw SQL on MariaDB.
            with transaction.atomic():
                g.save()
            gid = int(g.id)
            oracle_to_geo[r.hierarch_place_id] = gid
            geo_by_pk[gid] = g
            existing_guid_to_id[guid] = gid
            stats.geographies_created += 1
        except Exception as exc:  # noqa: BLE001
            nm = oracle_type_name_to_rank_item_name(r.type_name)
            _fail_fast(
                "geography.save",
                str(exc),
                cause=exc,
                owner=owner,
                treedef_id=treedef_id,
                hierarch_place_id=r.hierarch_place_id,
                placename=r.placename,
                place_id_partof=r.place_id_partof,
                oracle_type_name_raw=r.type_name,
                logical_rank=nm,
                definitionitem_id=int(di.id),
                definitionitem_name=getattr(di, "name", None),
                parent_geography_id=int(parent_geo.id) if parent_geo is not None else None,
                parent_geography_name=(getattr(parent_geo, "name", None) or "")[:200]
                if parent_geo is not None
                else None,
                rankid=getattr(di, "rankid", None),
                fullname=fullname,
                guid=guid,
                loop_index=i,
                total_geography=total_g,
                dry_run=dry_run,
            )

        if i == 1 or i % 500 == 0 or i == total_g:
            elapsed = time.perf_counter() - t_loop
            pct = 100.0 * i / total_g if total_g else 100.0
            eta = _eta_remaining(elapsed, i, total_g)
            err_n = len(stats.errors)
            err_snip = (" last_error=%r" % (stats.errors[-1][:160],)) if err_n else ""
            _progress_log(
                "oracle_geography | geography | owner=%s | %s/%s (%.1f%%) created=%s skipped=%s errors=%s%s elapsed=%s eta~%s",
                owner,
                i,
                total_g,
                pct,
                stats.geographies_created,
                stats.geographies_skipped_existing,
                err_n,
                err_snip,
                _format_duration(elapsed),
                eta,
            )

    _progress_log(
        "oracle_geography | geography | owner=%s | done rows=%s created=%s skipped=%s errors=%s total_elapsed=%s",
        owner,
        total_g,
        stats.geographies_created,
        stats.geographies_skipped_existing,
        len(stats.errors),
        _format_duration(time.perf_counter() - t_load),
    )
    return stats, oracle_to_geo


def _deepest_geography_for_place(
    oracle_cursor: Any,
    owner: str,
    place_id: int,
    oracle_hid_to_specify_geo: dict[int, int],
    geo_rankid_by_pk: dict[int, int],
) -> int | None:
    """Pick mapped ``Geography`` with highest ``rankid`` for this ``PLACE`` (no ORM in the loop)."""
    o = owner.upper()
    sql = f"""
    SELECT php.HIERACHICAL_PLACE_ID
      FROM {o}.place_hierachical_place php
     WHERE php.place_id = :pid
    """
    oracle_cursor.execute(sql, {"pid": place_id})
    best_geo: int | None = None
    best_rank = -1

    for (hid,) in oracle_cursor.fetchall():
        if hid is None:
            continue
        gid = oracle_hid_to_specify_geo.get(int(hid))
        if gid is None:
            continue
        rk = geo_rankid_by_pk.get(int(gid))
        if rk is None:
            continue
        if int(rk) > best_rank:
            best_rank = int(rk)
            best_geo = int(gid)
    return best_geo


def _parse_dms_coordinate(coordinate_string: str | None) -> tuple[float | None, float | None]:
    """Parse DMS strings like ``59°48.185'N 10°44.478'E`` into decimal degrees."""
    if not coordinate_string:
        return None, None
    pattern = re.compile(
        r"(\d+)[°º]\s*([\d.]+)[''′]\s*([NS])\s+"
        r"(\d+)[°º]\s*([\d.]+)[''′]\s*([EW])",
        re.IGNORECASE,
    )
    m = pattern.search(coordinate_string)
    if not m:
        return None, None
    try:
        lat_deg, lat_min, lat_hem = float(m.group(1)), float(m.group(2)), m.group(3).upper()
        lon_deg, lon_min, lon_hem = float(m.group(4)), float(m.group(5)), m.group(6).upper()
        lat = lat_deg + lat_min / 60.0
        lon = lon_deg + lon_min / 60.0
        if lat_hem == "S":
            lat = -lat
        if lon_hem == "W":
            lon = -lon
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
    except (ValueError, TypeError):
        pass
    return None, None


def _parse_dms_coordinate_range(
    coordinate_string: str | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Parse DMS range strings like ``17°7'-17°8'N 25°3'-25°4'W``.

    Returns ``(lat_low, lat_high, lon_low, lon_high)`` in signed decimal degrees.
    """
    if not coordinate_string:
        return None, None, None, None
    s = coordinate_string.strip()
    pattern = re.compile(
        r"(\d+)[°º]\s*([\d.]+)'\s*-\s*(\d+)[°º]\s*([\d.]+)'\s*([NS])\s+"
        r"(\d+)[°º]\s*([\d.]+)'\s*-\s*(\d+)[°º]\s*([\d.]+)'\s*([EW])",
        re.IGNORECASE,
    )
    m = pattern.search(s)
    if not m:
        return None, None, None, None
    try:
        lat_a = float(m.group(1)) + float(m.group(2)) / 60.0
        lat_b = float(m.group(3)) + float(m.group(4)) / 60.0
        lon_a = float(m.group(6)) + float(m.group(7)) / 60.0
        lon_b = float(m.group(8)) + float(m.group(9)) / 60.0
        lat_hemi = m.group(5).upper()
        lon_hemi = m.group(10).upper()
        if lat_hemi == "S":
            lat_a, lat_b = -lat_a, -lat_b
        if lon_hemi == "W":
            lon_a, lon_b = -lon_a, -lon_b
        lat_low, lat_high = (lat_a, lat_b) if lat_a <= lat_b else (lat_b, lat_a)
        lon_low, lon_high = (lon_a, lon_b) if lon_a <= lon_b else (lon_b, lon_a)
        if not (-90 <= lat_low <= 90 and -90 <= lat_high <= 90):
            return None, None, None, None
        if not (-180 <= lon_low <= 180 and -180 <= lon_high <= 180):
            return None, None, None, None
        return lat_low, lat_high, lon_low, lon_high
    except (TypeError, ValueError):
        return None, None, None, None


def _sanitize_lat_lng(
    lat: Any,
    lng: Any,
    coordinate_string: str | None,
) -> tuple[float | None, float | None]:
    """Return valid (lat, lng) decimals or None, with DMS-string fallback."""

    def _to_float(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    lat_f = _to_float(lat)
    lng_f = _to_float(lng)

    if lat_f is None or not (-90 <= lat_f <= 90):
        dms_lat, dms_lng = _parse_dms_coordinate(coordinate_string)
        if dms_lat is not None:
            return dms_lat, dms_lng
        if lat_f is not None and 100 < abs(lat_f) < 1000:
            candidate = lat_f - (int(lat_f / 100) * 100)
            if -90 <= candidate <= 90:
                lng_candidate = lng_f
                if lng_f is not None and not (-180 <= lng_f <= 180):
                    lng_candidate = None
                return candidate, lng_candidate
        return None, None

    if lng_f is not None and not (-180 <= lng_f <= 180):
        lng_f = None

    return lat_f, lng_f


def _to_decimal_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def locality_spatial_kwargs_from_musit_koordinate(coord: dict[str, Any]) -> dict[str, Any]:
    """Map Oracle ``KOORDINATE_PLACE`` row (from ``_fetch_first_coordinate``) to Specify ``Locality`` fields."""
    lat_raw = coord.get("latitude_l")
    lng_raw = coord.get("longitude_l")
    coord_str = coord.get("coordinate_string")
    lat, lng = _sanitize_lat_lng(lat_raw, lng_raw, coord_str)
    out: dict[str, Any] = {}

    datum = coord.get("datum")
    if datum:
        ds = str(datum).strip()
        if ds:
            out["datum"] = ds[:50]

    if lat is not None:
        out["latitude1"] = lat
    if lng is not None:
        out["longitude1"] = lng

    lat_h = _to_decimal_or_none(coord.get("latitude_h"))
    lon_h = _to_decimal_or_none(coord.get("longitude_h"))
    if lat_h is None or lon_h is None:
        # Many MUSIT rows store range bounds only in COORDINATE_STRING.
        rlat_low, rlat_high, rlon_low, rlon_high = _parse_dms_coordinate_range(coord_str)
        if lat is not None and rlat_low is not None and lat_h is None:
            # Prefer the opposite range endpoint as latitude2.
            lat_h = rlat_high if abs(lat - rlat_low) <= abs(lat - rlat_high) else rlat_low
        elif lat_h is None and rlat_high is not None:
            lat_h = rlat_high
        if lng is not None and rlon_low is not None and lon_h is None:
            lon_h = rlon_high if abs(lng - rlon_low) <= abs(lng - rlon_high) else rlon_low
        elif lon_h is None and rlon_high is not None:
            lon_h = rlon_high
    if lat_h is not None:
        out["latitude2"] = lat_h
    if lon_h is not None:
        out["longitude2"] = lon_h

    if coord_str:
        cs = str(coord_str).strip()
        if cs:
            out["lat1text"] = cs[:50]
            if len(cs) > 50:
                out["long1text"] = cs[50:100]

    alt_l = _to_decimal_or_none(coord.get("alt_l"))
    alt_h = _to_decimal_or_none(coord.get("alt_h"))
    if alt_l is not None and alt_h is not None:
        lo, hi = (alt_l, alt_h) if alt_l <= alt_h else (alt_h, alt_l)
        out["minelevation"] = lo
        out["maxelevation"] = hi
    elif alt_l is not None:
        out["minelevation"] = alt_l
        out["maxelevation"] = alt_l
    elif alt_h is not None:
        out["minelevation"] = alt_h
        out["maxelevation"] = alt_h

    alt_str = coord.get("altitude_string")
    if alt_str:
        vs = str(alt_str).strip()[:50]
        if vs:
            out["verbatimelevation"] = vs

    alt_unit = coord.get("altitude_unit")
    if alt_unit:
        us = str(alt_unit).strip()[:50]
        if us:
            out["originalelevationunit"] = us

    acc = _to_decimal_or_none(coord.get("accuracy"))
    if acc is not None:
        out["latlongaccuracy"] = acc

    prec = coord.get("precision")
    ca_alt = coord.get("ca_altitude")
    bits: list[str] = []
    if prec is not None:
        bits.append(f"MUSIT PRECISION={prec}")
    if ca_alt:
        ca = str(ca_alt).strip()
        if ca:
            bits.append(f"MUSIT CA_ALTITUDE={ca[:40]}")

    # UTM grid reference (MUSIT "Zone & Band" + grid square), e.g. "32 V".
    zone = coord.get("zone")
    belt = coord.get("belt")
    zone_str = str(zone).strip() if zone is not None else ""
    belt_str = str(belt).strip() if belt is not None else ""
    utm_zone = f"{zone_str}{belt_str}".strip()
    if utm_zone:
        bits.append(f"MUSIT UTM ZONE={utm_zone[:20]}")

    map_sheet = coord.get("map_sheet")
    if map_sheet:
        ms = str(map_sheet).strip()
        if ms:
            bits.append(f"MUSIT MAP_SHEET={ms[:40]}")

    # MUSIT coordinate-quality flags: "Ca coord" (approximate) and
    # "Coordinate added later" (georeferenced after collection).
    if _musit_flag_is_set(coord.get("ca_utm")):
        bits.append("MUSIT CA_COORD=1")
    if _musit_flag_is_set(coord.get("utm_senere")):
        bits.append("MUSIT COORD_ADDED_LATER=1")

    if bits:
        out["remarks"] = " ".join(bits)

    return out


def _musit_flag_is_set(value: Any) -> bool:
    """True for MUSIT boolean-ish flag columns stored as '1'/'Y'/'TRUE'."""
    if value is None:
        return False
    return str(value).strip().upper() in ("1", "Y", "TRUE")


def _fetch_place_text(oracle_cursor: Any, owner: str, place_id: int) -> tuple[str, str | None]:
    o = owner.upper()
    oracle_cursor.execute(f"SELECT place_name_agg FROM {o}.place WHERE place_id = :pid", {"pid": place_id})
    row = oracle_cursor.fetchone()
    agg = (row[0] or "").strip() if row else ""
    oracle_cursor.execute(
        f"""
        SELECT locality FROM (
          SELECT lp.locality
            FROM {o}.place_locality_place plp
            JOIN {o}.locality_place lp ON lp.locality_place_id = plp.locality_place_id
           WHERE plp.place_id = :pid
           ORDER BY lp.locality_place_id
        ) WHERE ROWNUM = 1
        """,
        {"pid": place_id},
    )
    r2 = oracle_cursor.fetchone()
    loc = (r2[0] or "").strip() if r2 else None
    return agg, loc


def _fetch_place_ecology_and_region(
    oracle_cursor: Any, owner: str, place_id: int
) -> tuple[str | None, str | None]:
    """Return ``(ecology_text, biogeographic_region_name)`` for a MUSIT ``PLACE_ID``.

    Ecology lives in the collection schema. The biogeographic region *name* lives in
    the shared ``MUSIT_NATHIST_FELLES`` schema, which the migration role may not have
    SELECT on — that lookup degrades to ``None`` instead of failing the object.
    """
    o = owner.upper()

    ecology: str | None = None
    oracle_cursor.execute(
        f"""
        SELECT ecology FROM (
          SELECT e.ecology
            FROM {o}.place_ecology_place pep
            JOIN {o}.ecology_place e ON e.ecology_place_id = pep.ecology_place_id
           WHERE pep.place_id = :pid
             AND e.ecology IS NOT NULL
           ORDER BY e.ecology_place_id
        ) WHERE ROWNUM = 1
        """,
        {"pid": place_id},
    )
    row = oracle_cursor.fetchone()
    if row and row[0]:
        ecology = str(row[0]).strip() or None

    region: str | None = None
    try:
        oracle_cursor.execute(
            f"""
            SELECT bio_geo_region FROM (
              SELECT r.bio_geo_region
                FROM {o}.place_bio_geografisk_region pbr
                JOIN MUSIT_NATHIST_FELLES.bio_geografisk_region r
                  ON r.bio_geo_reg_id = pbr.bio_geografisk_region_id
               WHERE pbr.place_id = :pid
                 AND r.bio_geo_region IS NOT NULL
               ORDER BY r.bio_geo_reg_id
            ) WHERE ROWNUM = 1
            """,
            {"pid": place_id},
        )
        r2 = oracle_cursor.fetchone()
        if r2 and r2[0]:
            region = str(r2[0]).strip() or None
    except Exception:  # noqa: BLE001 — cross-schema lookup is a system boundary (grants/availability)
        region = None

    return ecology, region


def _fetch_first_coordinate(oracle_cursor: Any, owner: str, place_id: int) -> dict[str, Any | None]:
    """Pick the best ``KOORDINATE_PLACE`` row for a ``PLACE_ID`` (prefer numeric lat/long, then newest id)."""
    o = owner.upper()
    oracle_cursor.execute(
        f"""
        SELECT * FROM (
            SELECT
                kp.COORDINATE_STRING,
                kp.LATITUDE_L,
                kp.LONGITUDE_L,
                kp.DATUM,
                kp."PRECISION" AS MUSIT_PRECISION,
                kp.ACCURACY,
                kp.ALT_L,
                kp.ALT_H,
                kp.ALTITUDE_STRING,
                kp.ALTITUDE_UNIT,
                kp.CA_ALTITUDE,
                kp.LATITUDE_H,
                kp.LONGITUDE_H,
                kp.MAP_SHEET,
                kp.ZONE,
                kp.BELT,
                kp.CA_UTM,
                kp.UTM_SENERE,
                kp.KOORDINATE_PLACE_ID
              FROM {o}.koordinate_place kp
              JOIN {o}.koordinate_place_place kpp
                ON kpp.koordinate_place_id = kp.koordinate_place_id
             WHERE kpp.place_id = :pid
             ORDER BY
               CASE WHEN kp.LATITUDE_L IS NOT NULL AND kp.LONGITUDE_L IS NOT NULL THEN 0 ELSE 1 END,
               CASE WHEN kp.COORDINATE_STRING IS NOT NULL THEN 0 ELSE 1 END,
               kp.KOORDINATE_PLACE_ID DESC
        ) WHERE ROWNUM = 1
        """,
        {"pid": place_id},
    )
    r = oracle_cursor.fetchone()
    if not r:
        return {
            "coordinate_string": None,
            "latitude_l": None,
            "longitude_l": None,
            "datum": None,
            "precision": None,
            "accuracy": None,
            "alt_l": None,
            "alt_h": None,
            "altitude_string": None,
            "altitude_unit": None,
            "ca_altitude": None,
            "latitude_h": None,
            "longitude_h": None,
            "map_sheet": None,
            "zone": None,
            "belt": None,
            "ca_utm": None,
            "utm_senere": None,
            "koordinate_place_id": None,
        }
    return {
        "coordinate_string": r[0],
        "latitude_l": r[1],
        "longitude_l": r[2],
        "datum": r[3],
        "precision": r[4],
        "accuracy": r[5],
        "alt_l": r[6],
        "alt_h": r[7],
        "altitude_string": r[8],
        "altitude_unit": r[9],
        "ca_altitude": r[10],
        "latitude_h": r[11],
        "longitude_h": r[12],
        "map_sheet": r[13],
        "zone": r[14],
        "belt": r[15],
        "ca_utm": r[16],
        "utm_senere": r[17],
        "koordinate_place_id": r[18],
    }


def _iter_referenced_place_ids(oracle_cursor: Any, owner: str) -> list[int]:
    o = owner.upper()
    oracle_cursor.execute(
        f"""
        SELECT DISTINCT place_id FROM (
          SELECT place_id FROM {o}.place_object_role WHERE place_id IS NOT NULL
          UNION
          SELECT place_id FROM {o}.place_event_role WHERE place_id IS NOT NULL
        )
        """
    )
    return [int(x[0]) for x in oracle_cursor.fetchall() if x[0] is not None]


def _place_locality_guid(owner_lower: str, place_id: int, discipline_pk: int) -> str:
    g = f"urn:oracle:{owner_lower}:place:{place_id}:d{discipline_pk}"
    return g[:128] if len(g) > 128 else g


def _prefetch_locality_ids_for_places(
    *,
    owner_lower: str,
    discs: list[Any],
    treedef_id: int,
    place_ids: list[int],
) -> dict[tuple[int, str], int]:
    """Load existing ``Locality`` ids for (discipline, place GUID) keys — one small batch, not whole table."""
    from specifyweb.specify.models import Locality

    out: dict[tuple[int, str], int] = {}
    if not place_ids:
        return out
    guid_chunk = 800
    for disc in discs:
        if disc is None or int(disc.geographytreedef_id or 0) != int(treedef_id):
            continue
        did = int(disc.id)
        guids = [_place_locality_guid(owner_lower, pid, did) for pid in place_ids]
        for gi in range(0, len(guids), guid_chunk):
            part = guids[gi : gi + guid_chunk]
            for guid, lid in Locality.objects.filter(discipline_id=did, guid__in=part).values_list("guid", "id"):
                out[(did, str(guid))] = int(lid)
    return out


def oracle_hid_map_from_specify_geography(*, owner: str, treedef_id: int) -> dict[int, int]:
    """Rebuild Oracle HIERARCH_PLACE_ID → Specify ``Geography`` id from ``urn:oracle:…:hpo:`` GUIDs."""
    from specifyweb.specify.models import Geography

    prefix = f"urn:oracle:{owner.lower()}:hpo:"
    out: dict[int, int] = {}
    for g in Geography.objects.filter(definition_id=treedef_id, guid__startswith=prefix).iterator(chunk_size=4000):
        gn = (g.guid or "").strip()
        if not gn.startswith(prefix):
            continue
        tail = gn[len(prefix) :].strip()
        if not tail:
            continue
        num = ""
        for ch in tail:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            continue
        try:
            hid = int(num)
        except ValueError:
            continue
        out[hid] = int(g.id)
    return out


@dataclass
class LocalityLoadStats:
    owner: str
    places_seen: int = 0
    localities_created: int = 0
    localities_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def load_localities_for_referenced_places(
    *,
    oracle_cursor: Any,
    owner: str,
    oracle_hid_to_specify_geo: dict[int, int],
    discipline_ids: list[int],
    treedef_id: int,
    run_ts: str,
    dry_run: bool,
    max_places: int | None = None,
    locality_skip_first_n_places: int = 0,
    locality_place_batch_size: int = 2048,
) -> LocalityLoadStats:
    """Create one ``Locality`` per (place, discipline) for referenced ``PLACE_ID`` rows.

    Referenced ``PLACE_ID`` values are processed in **sorted** order so ``locality_skip_first_n_places`` is stable
    across runs. Existing rows are resolved in **batches** (bounded memory); re-running the flow
    skips inserts and refreshes placemap rows without loading millions of keys into RAM.
    """
    from specifyweb.specify.models import Discipline, Geography, Locality

    from flows.lib.migration_oracle_placemap import upsert_placemap_row

    stats = LocalityLoadStats(owner=owner)
    t_loc = time.perf_counter()
    t_pids = time.perf_counter()
    raw_places = sorted(set(_iter_referenced_place_ids(oracle_cursor, owner)))
    n_oracle_distinct = len(raw_places)
    pids_all = raw_places
    if max_places is not None:
        pids_all = pids_all[: max_places]
    skip_n = max(0, int(locality_skip_first_n_places))
    if skip_n:
        remaining = max(0, len(pids_all) - skip_n)
        _progress_log(
            "oracle_geography | locality | owner=%s | resume locality_skip_first_n_places=%s "
            "(sorted PLACE_ID order; places_in_run_before_skip=%s remaining_after_skip=%s)",
            owner,
            skip_n,
            len(pids_all),
            remaining,
        )
        pids_all = pids_all[skip_n:]
    _progress_log(
        "oracle_geography | locality | owner=%s | oracle_distinct_place_ids=%s places_in_this_run=%s "
        "(Oracle query %s)%s",
        owner,
        n_oracle_distinct,
        len(pids_all),
        _format_duration(time.perf_counter() - t_pids),
        f" max_places={max_places}" if max_places is not None else "",
    )
    pids = pids_all

    discs = [Discipline.objects.filter(pk=i).first() for i in discipline_ids]
    discs = [d for d in discs if d is not None]

    t_gr = time.perf_counter()
    geo_rankid_by_pk: dict[int, int] = {}
    raw_geo_ids = list({int(x) for x in oracle_hid_to_specify_geo.values() if x is not None})
    _chunk = 8000
    for gi in range(0, len(raw_geo_ids), _chunk):
        part = raw_geo_ids[gi : gi + _chunk]
        geo_rankid_by_pk.update(dict(Geography.objects.filter(pk__in=part).values_list("id", "rankid")))

    batch_sz = max(100, min(int(locality_place_batch_size), 50_000))
    total_p = len(pids)
    _progress_log(
        "oracle_geography | locality | owner=%s | prefetches: geography_rank rows=%s (%s) locality_batch_size=%s dry_run=%s — starting per-place loop",
        owner,
        len(geo_rankid_by_pk),
        _format_duration(time.perf_counter() - t_gr),
        batch_sz,
        dry_run,
    )

    if total_p == 0:
        _progress_log(
            "oracle_geography | locality | owner=%s | no referenced places; skipping loop (total_elapsed=%s)",
            owner,
            _format_duration(time.perf_counter() - t_loc),
        )
        return stats

    t_loop = time.perf_counter()
    owner_lower = owner.lower()
    batch_counter = 0
    for batch_start in range(0, len(pids), batch_sz):
        batch_counter += 1
        if batch_counter % 40 == 0:
            close_old_connections()
        batch_pids = pids[batch_start : batch_start + batch_sz]
        locality_cache = _prefetch_locality_ids_for_places(
            owner_lower=owner_lower,
            discs=discs,
            treedef_id=treedef_id,
            place_ids=batch_pids,
        )
        for j, pid in enumerate(batch_pids):
            n = batch_start + j + 1
            if n == 1 or n % 250 == 0 or n == total_p:
                elapsed = time.perf_counter() - t_loop
                pct = 100.0 * n / total_p if total_p else 100.0
                eta = _eta_remaining(elapsed, n, total_p)
                _progress_log(
                    "oracle_geography | locality | owner=%s | places %s/%s (%.2f%%) created=%s skipped=%s errors=%s loop_elapsed=%s eta~%s",
                    owner,
                    n,
                    total_p,
                    pct,
                    stats.localities_created,
                    stats.localities_skipped,
                    len(stats.errors),
                    _format_duration(elapsed),
                    eta,
                )
            stats.places_seen += 1
            agg, loc_text = _fetch_place_text(oracle_cursor, owner, pid)
            coord = _fetch_first_coordinate(oracle_cursor, owner, pid)
            geo_id = _deepest_geography_for_place(
                oracle_cursor, owner, pid, oracle_hid_to_specify_geo, geo_rankid_by_pk
            )
            locality_name = (loc_text or agg or f"Place {pid}")[:1024]
            verbatim = agg[:8192] if agg else None

            for disc in discs:
                if disc is None:
                    continue
                if int(disc.geographytreedef_id or 0) != int(treedef_id):
                    continue
                guid_loc = _place_locality_guid(owner_lower, pid, int(disc.id))
                loc_key = (int(disc.id), guid_loc)
                existing_lid = locality_cache.get(loc_key)
                if existing_lid is not None:
                    stats.localities_skipped += 1
                    try:
                        upsert_placemap_row(
                            source_owner=owner.upper(),
                            source_kind="place",
                            source_id=str(pid),
                            specify_geography_id=geo_id,
                            specify_locality_id=existing_lid,
                            specify_discipline_id=int(disc.id),
                            run_ts=run_ts,
                            dry_run=dry_run,
                        )
                    except Exception as exc:  # noqa: BLE001
                        _fail_fast(
                            "locality.placemap_existing",
                            str(exc),
                            cause=exc,
                            owner=owner,
                            treedef_id=treedef_id,
                            place_id=pid,
                            discipline_id=int(disc.id),
                            existing_locality_id=existing_lid,
                            specify_geography_id=geo_id,
                            locality_name=locality_name[:300],
                            loop_place_index=n,
                            total_places=total_p,
                        )
                    continue
                if dry_run:
                    stats.localities_created += 1
                    continue
                try:
                    # Specify ``Locality`` has no ``VerbatimLocality`` (that lives on ``CollectingEvent``).
                    # Oracle ``place_name_agg`` → ``text1`` (migration slot).
                    loc_kwargs: dict[str, Any] = {
                        "discipline_id": int(disc.id),
                        "localityname": locality_name,
                        "geography_id": geo_id,
                        "srclatlongunit": 0,
                        "guid": guid_loc,
                    }
                    loc_kwargs.update(locality_spatial_kwargs_from_musit_koordinate(coord))
                    if verbatim:
                        loc_kwargs["text1"] = verbatim
                    loc = Locality(**loc_kwargs)
                    loc.save()
                    stats.localities_created += 1
                    locality_cache[loc_key] = int(loc.id)
                    upsert_placemap_row(
                        source_owner=owner.upper(),
                        source_kind="place",
                        source_id=str(pid),
                        specify_geography_id=geo_id,
                        specify_locality_id=int(loc.id),
                        specify_discipline_id=int(disc.id),
                        run_ts=run_ts,
                        dry_run=False,
                    )
                except Exception as exc:  # noqa: BLE001
                    _fail_fast(
                        "locality.save_or_placemap",
                        str(exc),
                        cause=exc,
                        owner=owner,
                        treedef_id=treedef_id,
                        place_id=pid,
                        discipline_id=int(disc.id),
                        specify_geography_id=geo_id,
                        locality_name=locality_name[:300],
                        oracle_place_text_snip=(verbatim or "")[:400] if verbatim else None,
                        guid=guid_loc,
                        latitude1=loc_kwargs.get("latitude1"),
                        longitude1=loc_kwargs.get("longitude1"),
                        coordinate=coord,
                        loop_place_index=n,
                        total_places=total_p,
                    )

    _progress_log(
        "oracle_geography | locality | owner=%s | done places=%s created=%s skipped=%s errors=%s total_elapsed=%s",
        owner,
        stats.places_seen,
        stats.localities_created,
        stats.localities_skipped,
        len(stats.errors),
        _format_duration(time.perf_counter() - t_loc),
    )
    return stats


def biology_discipline_ids_for_shared_treedef(treedef_id: int) -> list[int]:
    """Discipline PKs that use this geography treedef (biology collections)."""
    from specifyweb.specify.models import Discipline

    ids: list[int] = []
    for d in Discipline.objects.filter(geographytreedef_id=treedef_id).order_by("id"):
        if getattr(d, "is_geo", lambda: False)():
            continue
        ids.append(int(d.id))
    return ids
