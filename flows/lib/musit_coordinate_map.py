"""Map MUSIT ``KOORDINATE_PLACE`` / ``DERIVED_COORDINATES`` rows to Specify locality fields.

Layout (v8)
-----------
- ``latitude1`` / ``longitude1`` — WGS decimal degrees only
- ``lat1text`` / ``long1text`` — text mirror of those WGS values (LatLonUI)
- ``latLongType`` — ``Point`` when WGS is present (O-V has no real rectangles)
- ``text3`` — MGRS / grid verbatim string
- ``text5`` — UTM as GeoJSON (clean contract for future Specify UTM support)
- ``text4`` — full migration audit JSON
- ``yesNo1`` / ``yesNo2`` / ``yesNo3`` — Ca coordinate / Coordinate added later / Ca altitude

Copy-only: no grid conversion, DMS parsing, or reprojection during migration.
"""

from __future__ import annotations

import json
import re
from typing import Any

COORD_CONFLICT_THRESHOLD_DEG = 0.01
LAT1TEXT_MAX_LEN = 50
REMARKS_JSON_MAX_LEN = 4000
MAPPING_VERSION = "musit-coordinates-v8"
_KNOWN_GEODETIC_DATUMS = frozenset({"WGS84", "ED50", "ETRS89", "EUREF89", "OSGB36", "NAD83", "NAD27"})
# Norwegian/MGRS-ish grids: "ML 796,697", "CS 163,372", "NM 71,56", "MK 793-797,676-680"
_MGRS_LIKE = re.compile(
    r"^[A-Z]{1,3}\s+\d[\d\-]*(?:\s*,\s*\d[\d\-]*)?$",
    re.IGNORECASE,
)


def _to_decimal_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def musit_flag_is_set(value: Any) -> bool:
    """True for MUSIT boolean-ish flag columns stored as '1'/'Y'/'TRUE'."""
    if value is None:
        return False
    return str(value).strip().upper() in ("1", "Y", "TRUE")


def musit_flag_to_bool(value: Any) -> bool | None:
    """Map MUSIT flag column to tri-state bool (``None`` when unset/unknown)."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if s == "":
        return None
    if s in ("1", "Y", "TRUE", "T"):
        return True
    if s in ("0", "N", "FALSE", "F"):
        return False
    return None


def stored_lat_lng_pair(lat: Any, lng: Any) -> tuple[float | None, float | None]:
    """Return a stored decimal pair only when both values are already valid degrees."""
    lat_f = _to_decimal_or_none(lat)
    lng_f = _to_decimal_or_none(lng)
    if lat_f is None or lng_f is None:
        return None, None
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None, None
    return lat_f, lng_f


def coords_materially_differ(
    lat_a: float,
    lon_a: float,
    lat_b: float,
    lon_b: float,
    *,
    threshold: float = COORD_CONFLICT_THRESHOLD_DEG,
) -> bool:
    return abs(lat_a - lat_b) > threshold or abs(lon_a - lon_b) > threshold


def normalize_musit_datum(raw: Any) -> tuple[str | None, str | None]:
    """Map MUSIT datum text to Specify ``datum`` when recognizable; return raw for audit."""
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    upper = text.upper().replace(" ", "")
    if upper in {"WGS84", "WGS-84"}:
        return "WGS84", text
    if upper in {"ED50", "ED-50"}:
        return "ED50", text
    if upper in {"EUREF89", "ETRS89", "ETRS-89"}:
        return "ETRS89", text
    if upper in _KNOWN_GEODETIC_DATUMS:
        return upper, text
    if text.isdigit():
        return None, text
    return None, text


def non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def verbatim_coordinate_string(coord: dict[str, Any]) -> str | None:
    """Primary verbatim grid/coordinate text from MUSIT (no transformation)."""
    for key in ("coordinate_string", "coordinate_string_actual", "mgrs_l"):
        text = non_empty_text(coord.get(key))
        if text:
            return text
    return None


def looks_like_mgrs_or_grid(text: str) -> bool:
    return bool(_MGRS_LIKE.match(text.strip()))


def mgrs_verbatim_from_coord(coord: dict[str, Any]) -> str | None:
    """Return MGRS/grid text for ``Locality.text3``, or None."""
    for key in ("mgrs_l", "mgrs_h"):
        text = non_empty_text(coord.get(key))
        if text:
            return text
    term = (non_empty_text(coord.get("coordinate_type_term")) or "").upper()
    for key in ("coordinate_string", "coordinate_string_actual"):
        text = non_empty_text(coord.get(key))
        if not text:
            continue
        if term == "MGRS" or looks_like_mgrs_or_grid(text):
            return text
    return None


def _format_axis_number(value: Any) -> float | int | None:
    num = _to_decimal_or_none(value)
    if num is None:
        return None
    if float(num).is_integer():
        return int(num)
    return num


def _format_wgs_text(value: float) -> str:
    text = f"{value}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
    return text[:LAT1TEXT_MAX_LEN]


def _utm_zone_int(coord: dict[str, Any]) -> int | None:
    for key in ("zone", "dc_zone"):
        raw = coord.get(key)
        if raw is None:
            continue
        try:
            zone = int(float(raw))
        except (TypeError, ValueError):
            continue
        if 1 <= zone <= 60:
            return zone
    return None


def _utm_band(coord: dict[str, Any]) -> str | None:
    for key in ("belt", "dc_band"):
        text = non_empty_text(coord.get(key))
        if text:
            return text[:8]
    return None


def _epsg_for_utm_zone(zone: int | None, band: str | None) -> str | None:
    if zone is None:
        return None
    # Latitude bands C–M are southern hemisphere in MGRS; N–X northern.
    southern = bool(band and band[:1].upper() < "N")
    base = 32700 if southern else 32600
    return f"EPSG:{base + zone}"


def pick_utm_axes(coord: dict[str, Any]) -> tuple[float | int | None, float | int | None, str | None]:
    """Return easting, northing, and source label from the best available UTM pair."""
    for source, x_key, y_key in (
        ("kp", "utm_x", "utm_y"),
        ("dc", "dc_utm_x", "dc_utm_y"),
        ("kp_utm33", "utm33_x", "utm33_y"),
        ("dc_utm33", "dc_utm33_x", "dc_utm33_y"),
    ):
        x = _format_axis_number(coord.get(x_key))
        y = _format_axis_number(coord.get(y_key))
        if x is not None and y is not None:
            return x, y, source
    return None, None, None


def build_utm_geojson(coord: dict[str, Any]) -> dict[str, Any] | None:
    """Clean GeoJSON Feature for ``Locality.text5`` (Point; optional high corner in properties)."""
    x, y, source = pick_utm_axes(coord)
    if x is None or y is None or source is None:
        return None

    zone = _utm_zone_int(coord)
    band = _utm_band(coord)
    props: dict[str, Any] = {
        "source": source,
        "crs": _epsg_for_utm_zone(zone, band),
        "zone": zone,
        "band": band,
    }

    x_h = _format_axis_number(coord.get("utm_x_h"))
    y_h = _format_axis_number(coord.get("utm_y_h"))
    if x_h is not None and y_h is not None and (x_h != x or y_h != y):
        props["high"] = [x_h, y_h]

    # Prefer dedicated UTM33 axes in properties when present alongside primary.
    u33_x = _format_axis_number(coord.get("utm33_x") or coord.get("dc_utm33_x"))
    u33_y = _format_axis_number(coord.get("utm33_y") or coord.get("dc_utm33_y"))
    if u33_x is not None and u33_y is not None and source not in {"kp_utm33", "dc_utm33"}:
        props["utm33"] = [u33_x, u33_y]

    # Drop null property values for a compact payload.
    props = {k: v for k, v in props.items() if v is not None}

    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": props,
    }


def apply_verbatim_coordinate_fields(out: dict[str, Any], verbatim: str) -> None:
    """Backward-compatible helper: store verbatim coordinate text on ``text3``."""
    out["text3"] = verbatim


def _json_number_or_none(value: Any) -> float | int | None:
    return _format_axis_number(value)


def build_coordinate_audit_json(
    coord: dict[str, Any],
    *,
    owner: str | None,
    place_id: int | None,
    primary_source: str | None,
    conflict: dict[str, Any] | None,
) -> dict[str, Any]:
    kp_lat, kp_lon = stored_lat_lng_pair(coord.get("latitude_l"), coord.get("longitude_l"))
    dc_lat, dc_lon = stored_lat_lng_pair(coord.get("dc_latitude"), coord.get("dc_longitude"))
    kp_datum_norm, kp_datum_raw = normalize_musit_datum(coord.get("datum"))
    dc_datum_norm, dc_datum_raw = normalize_musit_datum(coord.get("dc_datum"))

    payload: dict[str, Any] = {
        "musit": {
            "owner": owner.upper() if owner else None,
            "place_id": place_id,
            "koordinate_place_id": coord.get("koordinate_place_id"),
            "coordinate_type": coord.get("coordinate_type"),
            "coordinate_type_term": coord.get("coordinate_type_term"),
        },
        "verbatim": {
            "coordinate_string": non_empty_text(coord.get("coordinate_string")),
            "coordinate_string_actual": non_empty_text(coord.get("coordinate_string_actual")),
            "mgrs_l": non_empty_text(coord.get("mgrs_l")),
            "mgrs_h": non_empty_text(coord.get("mgrs_h")),
        },
        "stored": {
            "kp_lat": kp_lat,
            "kp_lon": kp_lon,
            "kp_lat_h": _json_number_or_none(coord.get("latitude_h")),
            "kp_lon_h": _json_number_or_none(coord.get("longitude_h")),
            "dc_lat": dc_lat,
            "dc_lon": dc_lon,
            "dc_lat_wgs84": _json_number_or_none(coord.get("dc_lat_wgs84")),
            "dc_lon_wgs84": _json_number_or_none(coord.get("dc_lon_wgs84")),
            "primary_source": primary_source,
            "kp_datum": kp_datum_norm,
            "kp_datum_raw": kp_datum_raw,
            "dc_datum": dc_datum_norm,
            "dc_datum_raw": dc_datum_raw,
        },
        "utm": {
            "kp": {
                "zone": _json_number_or_none(coord.get("zone")),
                "belt": non_empty_text(coord.get("belt")),
                "x": _json_number_or_none(coord.get("utm_x")),
                "y": _json_number_or_none(coord.get("utm_y")),
                "x_h": _json_number_or_none(coord.get("utm_x_h")),
                "y_h": _json_number_or_none(coord.get("utm_y_h")),
                "utm33_x": _json_number_or_none(coord.get("utm33_x")),
                "utm33_y": _json_number_or_none(coord.get("utm33_y")),
            },
            "dc": {
                "zone": _json_number_or_none(coord.get("dc_zone")),
                "band": non_empty_text(coord.get("dc_band")),
                "x": _json_number_or_none(coord.get("dc_utm_x")),
                "y": _json_number_or_none(coord.get("dc_utm_y")),
                "utm33_x": _json_number_or_none(coord.get("dc_utm33_x")),
                "utm33_y": _json_number_or_none(coord.get("dc_utm33_y")),
            },
        },
        "uncertainty": {
            "musit_precision_m": _json_number_or_none(coord.get("precision")),
        },
        "flags": {
            "ca_utm": musit_flag_to_bool(coord.get("ca_utm")),
            "utm_senere": musit_flag_to_bool(coord.get("utm_senere")),
            "ca_altitude": musit_flag_to_bool(coord.get("ca_altitude")),
        },
        "migration_meta": {
            "mapping_version": MAPPING_VERSION,
        },
    }
    if conflict:
        payload["stored"]["conflict"] = conflict
    return payload


def _utm_zone_from_coord(coord: dict[str, Any]) -> str | None:
    zone = coord.get("zone")
    belt = coord.get("belt")
    zone_str = str(zone).strip() if zone is not None else ""
    belt_str = str(belt).strip() if belt is not None else ""
    utm_zone = f"{zone_str}{belt_str}".strip()
    if utm_zone and not zone_str.isdigit():
        return utm_zone[:20]
    if zone_str.isdigit() and belt_str:
        return utm_zone[:20]
    return None


def build_coordinate_remarks_payload(
    coord: dict[str, Any],
    *,
    conflict: dict[str, Any] | None = None,
    verbatim_non_mgrs: str | None = None,
    kp_datum_unmapped: str | None = None,
) -> dict[str, Any] | None:
    """Structured migration notes for ``Locality.remarks`` (machine-readable JSON)."""
    notes: dict[str, Any] = {}
    if verbatim_non_mgrs:
        notes["verbatim_coordinate"] = verbatim_non_mgrs[:200]

    utm_zone = _utm_zone_from_coord(coord)
    if utm_zone:
        notes["utm_zone"] = utm_zone

    map_sheet = non_empty_text(coord.get("map_sheet"))
    if map_sheet:
        notes["map_sheet"] = map_sheet[:40]

    if conflict:
        notes["coord_conflict"] = conflict

    if kp_datum_unmapped:
        notes["kp_datum_unmapped"] = kp_datum_unmapped[:40]

    if not notes:
        return None

    return {
        "migration_meta": {"kind": "musit-coordinate-notes", "version": 2},
        "notes": notes,
    }


def format_locality_remarks_json(payload: dict[str, Any]) -> str:
    """Serialize remarks payload to compact JSON, staying within Specify field length."""
    drop_order = (
        "map_sheet",
        "utm_zone",
        "kp_datum_unmapped",
        "verbatim_coordinate",
        "coord_conflict",
    )

    def _dump() -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    text = _dump()
    while len(text) > REMARKS_JSON_MAX_LEN:
        notes = payload.get("notes")
        if not isinstance(notes, dict) or not notes:
            payload = {
                "migration_meta": payload.get("migration_meta")
                or {"kind": "musit-coordinate-notes", "version": 2},
                "notes": {"truncated": True},
            }
            text = _dump()
            break
        dropped = False
        for key in drop_order:
            if key in notes:
                del notes[key]
                dropped = True
                break
        if not dropped:
            notes.clear()
        text = _dump()
    return text


def locality_spatial_kwargs_from_musit_koordinate(
    coord: dict[str, Any],
    *,
    owner: str | None = None,
    place_id: int | None = None,
) -> dict[str, Any]:
    """Map MUSIT coordinate rows to Specify ``Locality`` fields (copy-only, no conversion)."""
    out: dict[str, Any] = {}

    kp_lat, kp_lon = stored_lat_lng_pair(coord.get("latitude_l"), coord.get("longitude_l"))
    dc_lat, dc_lon = stored_lat_lng_pair(coord.get("dc_latitude"), coord.get("dc_longitude"))
    if dc_lat is None or dc_lon is None:
        wgs_lat, wgs_lon = stored_lat_lng_pair(coord.get("dc_lat_wgs84"), coord.get("dc_lon_wgs84"))
        if wgs_lat is not None and wgs_lon is not None:
            dc_lat, dc_lon = wgs_lat, wgs_lon

    primary_source: str | None = None
    lat1: float | None = None
    lon1: float | None = None
    if dc_lat is not None and dc_lon is not None:
        lat1, lon1 = dc_lat, dc_lon
        primary_source = "dc"
    elif kp_lat is not None and kp_lon is not None:
        lat1, lon1 = kp_lat, kp_lon
        primary_source = "kp"

    if lat1 is not None and lon1 is not None:
        out["latitude1"] = lat1
        out["longitude1"] = lon1
        out["lat1text"] = _format_wgs_text(lat1)
        out["long1text"] = _format_wgs_text(lon1)
        out["latlongtype"] = "Point"

    # Do not map latitude_h/longitude_h or utm_*_h into lat2/lon2 — O-V has no
    # complete WGS rectangles; UTM high corners are MGRS cell extents.

    mgrs = mgrs_verbatim_from_coord(coord)
    if mgrs:
        out["text3"] = mgrs

    utm_geojson = build_utm_geojson(coord)
    if utm_geojson:
        out["text5"] = json.dumps(utm_geojson, ensure_ascii=False, separators=(",", ":"), default=str)

    verbatim = verbatim_coordinate_string(coord)
    verbatim_non_mgrs: str | None = None
    if verbatim and not mgrs:
        verbatim_non_mgrs = verbatim
    elif verbatim and mgrs and verbatim != mgrs and not looks_like_mgrs_or_grid(verbatim):
        verbatim_non_mgrs = verbatim

    kp_datum_norm, kp_datum_raw = normalize_musit_datum(coord.get("datum"))
    dc_datum_norm, _dc_datum_raw = normalize_musit_datum(coord.get("dc_datum"))
    datum = dc_datum_norm or kp_datum_norm
    if datum:
        out["datum"] = datum[:50]

    conflict: dict[str, Any] | None = None
    if kp_lat is not None and kp_lon is not None and dc_lat is not None and dc_lon is not None:
        if coords_materially_differ(kp_lat, kp_lon, dc_lat, dc_lon):
            conflict = {"kp": [kp_lat, kp_lon], "dc": [dc_lat, dc_lon]}

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

    prec = _to_decimal_or_none(coord.get("precision"))
    if prec is not None:
        out["latlongaccuracy"] = prec

    ca_utm = musit_flag_to_bool(coord.get("ca_utm"))
    if ca_utm is not None:
        out["yesno1"] = ca_utm
    coord_later = musit_flag_to_bool(coord.get("utm_senere"))
    if coord_later is not None:
        out["yesno2"] = coord_later
    ca_alt = musit_flag_to_bool(coord.get("ca_altitude"))
    if ca_alt is not None:
        out["yesno3"] = ca_alt

    remarks_payload = build_coordinate_remarks_payload(
        coord,
        conflict=conflict,
        verbatim_non_mgrs=verbatim_non_mgrs,
        kp_datum_unmapped=kp_datum_raw if kp_datum_raw and not kp_datum_norm else None,
    )
    if remarks_payload:
        out["remarks"] = format_locality_remarks_json(remarks_payload)

    audit = build_coordinate_audit_json(
        coord,
        owner=owner,
        place_id=place_id,
        primary_source=primary_source,
        conflict=conflict,
    )
    out["text4"] = json.dumps(audit, ensure_ascii=False, default=str)

    return out


def empty_coordinate_bundle() -> dict[str, Any | None]:
    return {
        "coordinate_string": None,
        "coordinate_string_actual": None,
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
        "coordinate_type": None,
        "coordinate_type_term": None,
        "mgrs_l": None,
        "mgrs_h": None,
        "utm_x": None,
        "utm_y": None,
        "utm_x_h": None,
        "utm_y_h": None,
        "utm33_x": None,
        "utm33_y": None,
        "dc_latitude": None,
        "dc_longitude": None,
        "dc_datum": None,
        "dc_zone": None,
        "dc_band": None,
        "dc_utm_x": None,
        "dc_utm_y": None,
        "dc_utm33_x": None,
        "dc_utm33_y": None,
        "dc_lat_wgs84": None,
        "dc_lon_wgs84": None,
        "dc_coordinates_type": None,
    }
