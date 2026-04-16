"""Load Oracle MUSIT hierarchical places into Specify ``Geography`` and ``Locality`` (Django ORM)."""

from __future__ import annotations

import logging
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
    """Map MUSIT ``TYPES`` label column (name varies by schema) to ``GeographyTreeDefItem.Name``."""
    t = _norm_type(type_name)
    if not t:
        return "County"
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
            stats.errors.append(mr["error"])
            return stats, oracle_to_geo
        rank_items = _rank_items_by_name_lower(treedef_id)

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
        stats.errors.append("no root Geography (Earth) for treedef")
        return stats, oracle_to_geo
    geo_by_pk[int(earth.id)] = earth

    def _geo_model(pk: int) -> Any:
        if pk not in geo_by_pk:
            g = Geography.objects.filter(pk=pk).first()
            if g is not None:
                geo_by_pk[pk] = g
        return geo_by_pk.get(pk)

    def rank_for_row(r: HierRow) -> Any:
        nm = oracle_type_name_to_rank_item_name(r.type_name)
        return _resolve_rank_item(rank_items, nm)

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
        di = rank_for_row(r)
        if di is None:
            nm = oracle_type_name_to_rank_item_name(r.type_name)
            stats.errors.append(
                f"no rank for hierarch_place_id={r.hierarch_place_id} logical_rank={nm!r} "
                f"(see treedef rank keys log above)"
            )
            continue
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
            msg = f"hpo {r.hierarch_place_id}: {exc}"
            stats.errors.append(msg[:500])
            logger.warning(msg)

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


def _fetch_first_coordinate(oracle_cursor: Any, owner: str, place_id: int) -> dict[str, Any | None]:
    o = owner.upper()
    oracle_cursor.execute(
        f"""
        SELECT kp.COORDINATE_STRING, kp.LATITUDE_L, kp.LONGITUDE_L, kp.DATUM
          FROM {o}.koordinate_place kp
          JOIN {o}.koordinate_place_place kpp ON kpp.koordinate_place_id = kp.koordinate_place_id
         WHERE kpp.place_id = :pid AND ROWNUM = 1
        """,
        {"pid": place_id},
    )
    r = oracle_cursor.fetchone()
    if not r:
        return {"coordinate_string": None, "latitude_l": None, "longitude_l": None, "datum": None}
    return {
        "coordinate_string": r[0],
        "latitude_l": r[1],
        "longitude_l": r[2],
        "datum": r[3],
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
) -> LocalityLoadStats:
    """Create one ``Locality`` per (place, discipline) for referenced ``PLACE_ID`` rows."""
    from specifyweb.specify.models import Discipline, Geography, Locality

    from flows.lib.migration_oracle_placemap import upsert_placemap_row

    stats = LocalityLoadStats(owner=owner)
    t_loc = time.perf_counter()
    t_pids = time.perf_counter()
    pids = _iter_referenced_place_ids(oracle_cursor, owner)
    if max_places is not None:
        pids = pids[: max_places]
    _progress_log(
        "oracle_geography | locality | owner=%s | distinct referenced PLACE_ID count=%s (Oracle query %s)%s",
        owner,
        len(pids),
        _format_duration(time.perf_counter() - t_pids),
        f" max_places={max_places}" if max_places is not None else "",
    )

    discs = [Discipline.objects.filter(pk=i).first() for i in discipline_ids]
    discs = [d for d in discs if d is not None]

    t_gr = time.perf_counter()
    geo_rankid_by_pk: dict[int, int] = {}
    raw_geo_ids = list({int(x) for x in oracle_hid_to_specify_geo.values() if x is not None})
    _chunk = 8000
    for gi in range(0, len(raw_geo_ids), _chunk):
        part = raw_geo_ids[gi : gi + _chunk]
        geo_rankid_by_pk.update(dict(Geography.objects.filter(pk__in=part).values_list("id", "rankid")))

    place_guid_prefix = f"urn:oracle:{owner.lower()}:place:"
    locality_id_by_disc_guid: dict[tuple[int, str], int] = {}
    for disc in discs:
        for guid, lid in Locality.objects.filter(
            discipline_id=disc.id, guid__startswith=place_guid_prefix
        ).values_list("guid", "id"):
            locality_id_by_disc_guid[(int(disc.id), str(guid))] = int(lid)

    total_p = len(pids)
    _progress_log(
        "oracle_geography | locality | owner=%s | prefetches: geography_rank rows=%s (%s) existing_locality_guids=%s dry_run=%s — starting per-place loop",
        owner,
        len(geo_rankid_by_pk),
        _format_duration(time.perf_counter() - t_gr),
        len(locality_id_by_disc_guid),
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
    for n, pid in enumerate(pids, start=1):
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

        lat = coord.get("latitude_l")
        lng = coord.get("longitude_l")
        if lat is not None:
            try:
                lat = float(lat)
            except (TypeError, ValueError):
                lat = None
        if lng is not None:
            try:
                lng = float(lng)
            except (TypeError, ValueError):
                lng = None

        for disc in discs:
            if disc is None:
                continue
            if int(disc.geographytreedef_id or 0) != int(treedef_id):
                continue
            guid_loc = f"urn:oracle:{owner.lower()}:place:{pid}:d{disc.id}"
            if len(guid_loc) > 128:
                guid_loc = guid_loc[:128]
            loc_key = (int(disc.id), guid_loc)
            existing_lid = locality_id_by_disc_guid.get(loc_key)
            if existing_lid is not None:
                stats.localities_skipped += 1
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
                continue
            if dry_run:
                stats.localities_created += 1
                continue
            try:
                loc = Locality(
                    discipline_id=disc.id,
                    localityname=locality_name,
                    verbatimlocality=verbatim,
                    geography_id=geo_id,
                    latitude1=lat,
                    longitude1=lng,
                    srclatlongunit=0,
                    guid=guid_loc,
                    datum=(coord.get("datum") or "")[:50] if coord.get("datum") else None,
                )
                loc.save()
                stats.localities_created += 1
                locality_id_by_disc_guid[loc_key] = int(loc.id)
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
                stats.errors.append(f"place {pid} disc {disc.id}: {exc}"[:500])

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
