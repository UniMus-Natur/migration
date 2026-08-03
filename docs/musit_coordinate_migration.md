---
layout: default
title: MUSIT coordinate migration
nav_order: 9
---

# MUSIT → Specify coordinate storage

How vascular-plant specimen coordinates from Oracle MUSIT are written onto Specify **`Locality`** during migration.

| | |
|--|--|
| **Mapping code** | `flows/lib/musit_coordinate_map.py` |
| **Callers** | `musit_dataset_loader.py`, `oracle_geography_load.py` |
| **Mapping version** | `musit-coordinates-v8` (stored in `Locality.text4` audit JSON) |
| **UI** | `specify7-forms` — Locality / CollectingEvent / CollectingEventSub forms + botany schema labels |

Policy is **copy-only**: no DMS parsing, no grid→WGS conversion, no reprojection in the ETL. Prefer values already present as decimal degrees or structured UTM/MGRS in MUSIT.

## Source (Oracle)

Coordinates hang off a collecting **place**:

```
COLLECTING_EVENT
  → PLACE_EVENT_ROLE → PLACE
       → KOORDINATE_PLACE_PLACE → KOORDINATE_PLACE
            ↳ optional DERIVED_COORDINATES
```

Important columns on **`KOORDINATE_PLACE`** / **`DERIVED_COORDINATES`**:

| Source | Role |
|--------|------|
| `DERIVED_COORDINATES.LATITUDE` / `LONGITUDE` (or `LAT_WGS84` / `LONG_WGS84`) | Preferred WGS decimal degrees |
| `KOORDINATE_PLACE.LATITUDE_L` / `LONGITUDE_L` | Fallback WGS when derived missing |
| `COORDINATE_STRING`, `MGRS_L` / `MGRS_H` | Verbatim grid / MGRS |
| `UTM_X` / `UTM_Y`, `UTM_X_H` / `UTM_Y_H`, `ZONE`, `BELT` | UTM (+ optional cell high corner) |
| `DC_UTM_*` | Derived UTM axes |
| `DATUM` / derived datum | Geodetic datum |
| `PRECISION` | Uncertainty (metres) → `latLongAccuracy` |
| `CA_UTM`, `UTM_SENERE` | “Ca coordinate” / “Coordinate added later” flags |
| `LATITUDE_H` / `LONGITUDE_H` | Incomplete range leftovers — **not** mapped to Specify point 2 |

`KOORDINATE_PLACE_ID` is unique **only within one owner schema**. Always qualify with the schema (e.g. `MUSIT_BOTANIKK_FELLES`).

### Geometry shape in O–V data

For Oslo vascular plants (`institutioncode=O`, `collectioncode=V`), PROD analysis showed:

- **No** complete WGS rectangles (`latitude_h` + `longitude_h` both set).
- Most “high” UTM corners are **MGRS grid-cell extents** (e.g. 100 m / 1 km), not collecting polygons.
- Migration therefore sets Specify **`latLongType = Point`** whenever WGS decimals exist, and does **not** populate `latitude2` / `longitude2`.

## Specify `Locality` field layout (v8)

| Specify field | Content |
|---------------|---------|
| `latitude1` / `longitude1` | WGS decimal degrees only (prefer derived, else KP) |
| `lat1text` / `long1text` | Text mirror of those WGS values (for LatLonUI) |
| `latLongType` | `Point` when WGS is present |
| `latitude2` / `longitude2` | Unused for O–V (reserved for true Line/Rectangle) |
| `datum` | Normalized geodetic datum when recognizable (`WGS84`, `ED50`, `ETRS89`, …) |
| `latLongAccuracy` | MUSIT `PRECISION` (metres) |
| `text1` | Place-name aggregate (`PLACE.PLACE_NAME_AGG`) — not coordinates |
| `text2` | Norwegian biogeographic zone (picklist) — not coordinates |
| `text3` | **Grid ref. (MGRS)** — Norwegian MGRS verbatim (e.g. `CS 163,372`) |
| `text4` | Full migration **audit JSON** (provenance; hidden in UI) |
| `text5` | **UTM as GeoJSON** `Feature` (clean contract for future Specify UTM UI) |
| `yesNo1` | Ca coordinate (`CA_UTM`) |
| `yesNo2` | Coordinate added later (`UTM_SENERE`) |
| `yesNo3` | Ca altitude (`CA_ALTITUDE`) |
| `remarks` | Compact notes JSON when needed (conflicts, unmapped datum, non-MGRS verbatim, …) |

Elevation (`minElevation` / `maxElevation` / `verbatimElevation` / …) is mapped separately from altitude columns when present.

### Primary WGS choice

1. Use **`DERIVED_COORDINATES`** lat/lon when both are valid degrees.
2. Else use **`KOORDINATE_PLACE`** `LATITUDE_L` / `LONGITUDE_L`.
3. If both KP and derived disagree by more than **0.01°**, keep derived as primary and record the conflict in `remarks` and `text4`.

### `text3` — Grid ref. (MGRS)

Filled from, in order:

1. `MGRS_L` / `MGRS_H`
2. `COORDINATE_STRING` when `COORDINATE_TYPE` term is `MGRS`, or the string looks like a grid (`ML 796,697`, `NM 71,56`, …)

Non-MGRS verbatim (e.g. DMS-only strings with no decimal pair) is **not** forced into `text3`; it goes into `remarks.notes.verbatim_coordinate` and remains in `text4`.

### `text5` — UTM GeoJSON

Compact GeoJSON **Feature** (Point). Example:

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [316350, 3137250]
  },
  "properties": {
    "source": "dc",
    "crs": "EPSG:32628",
    "zone": 28,
    "band": "R",
    "high": [316450, 3137350]
  }
}
```

| Property | Meaning |
|----------|---------|
| `coordinates` | Easting, northing (best available: KP UTM → derived UTM → UTM33) |
| `source` | `kp`, `dc`, `kp_utm33`, or `dc_utm33` |
| `crs` | EPSG from zone + band when zone is known |
| `zone` / `band` | UTM zone and latitude band / belt |
| `high` | Optional second corner (often MGRS cell extent) |
| `utm33` | Optional parallel UTM33 axes when primary is not already UTM33 |

This is the stable field to read when Specify gains first-class UTM support. Do **not** treat the fat `text4` audit blob as that contract.

### `text4` — audit JSON

Machine-readable dump of MUSIT ids, verbatim strings, KP vs derived stored decimals, full UTM axes, flags, and `mapping_version`. Hidden in the UI; used for QA and remigration.

## What we deliberately do **not** do

- Put UTM easting/northing into `lat1text` / `long1text` (those are for WGS text entry).
- Put MGRS into `lat2text` or split grids across lat/long text fields.
- Set `latLongType` to MUSIT terms like `MGRS` / `UTM` (Specify expects Point / Line / Rectangle).
- Map incomplete `latitude_h` / `longitude_h` into Specify’s second point.
- Convert grids or DMS to WGS during migration.

## UI surfaces (`specify7-forms`)

| Form | Shows |
|------|--------|
| **Locality** | Where → Coordinates (LatLonUI, Geo Ref / Google Earth, datum/precision, Grid ref) → Elevation → collapsed GeoCoordDetails → Advanced (lat/long text, UTM GeoJSON, remarks) → Attachments |
| **LocalitySubForm** | Compact where + coordinates (no elevation / UTM / remarks) |
| **CollectingEventSub** (on Collection Object) | `latitude1` / `longitude1`, Grid ref. (MGRS), UTM GeoJSON, flags |
| **CollectingEvent** | Same coordinate fields as the subform |

Schema labels live in `specify7-forms/schema/botany/schema.en.json` (`Locality.text3` = Grid ref. (MGRS), `text5` = UTM (GeoJSON)). Regex validation uses UIFormatter `GridRefMGRS` (`specli formatter`).

**Note:** Specify 7 only collapses relationship subviews (`initialize="collapse=true"`), not same-table field groups. Advanced fields are therefore demoted to a trailing section; GeoCoordDetails is collapsed by default.

## Related docs

- Inventory of Oracle place/coordinate columns: [Oracle botany datasets — locality / coordinates](oracle_botany_datasets.md#3-locality-geography-coordinates)
- Geography tree (admin names): [Migration strategy](migration_strategy.md) and `flows/lib/oracle_geography_admin.py`
- Forms / schema deploy: [Specify forms & schema git sync](specify_forms_git_sync.md)
