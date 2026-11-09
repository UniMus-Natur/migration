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
| **Mapping version** | `musit-coordinates-v9` (stored in `Locality.text4` audit JSON) |
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

## Specify `Locality` field layout (v9)

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
| `text3` | **Grid ref. (MGRS)** — Norwegian MGRS verbatim (e.g. `CS 163,372`), without zone or band |
| `text4` | Full migration **audit JSON** (provenance; hidden in UI) |
| `yesNo1` | Ca coordinate (`CA_UTM`) |
| `yesNo2` | Coordinate added later (`UTM_SENERE`) |
| `yesNo3` | Ca altitude (`CA_ALTITUDE`) |
| `remarks` | Compact notes JSON when needed (conflicts, unmapped datum, non-MGRS verbatim, …) |

UTM and the shared grid zone live on the zero-or-one child **`LocalityDetail`**, not on `Locality` text fields.

| `LocalityDetail` field | Content |
|------------------------|---------|
| `utmEasting` / `utmNorthing` | Easting and northing in metres (decimal 19,2). Prefer KP UTM, else derived UTM, else UTM33 |
| `utmZone` | Zone number 1–60. Forced to `33` when the only axes are UTM33 |
| `mgrsZone` | MGRS latitude **band letter only**, e.g. `V` (zone number is on `utmZone`) |
| `utmDatum` | Datum of those UTM axes when recognizable (`WGS84`, `ED50`, `ETRS89`, …) |

`mgrsZone` (band) is omitted for a UTM33 fallback when the native zone is not 33, so a band from another zone is not attached to zone 33. The grid-cell high corner and any parallel UTM33 pair stay in the `text4` audit JSON. A locality that already has a `LocalityDetail` row is left unchanged; a later geography load inserts one only when the row is missing.

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

Zone and band are not prefixed onto `text3`. Zone → `LocalityDetail.utmZone`; band → `LocalityDetail.mgrsZone`.

## `LocalityDetail` — UTM and zone

Example for grid `CS 163,372` in zone 28, band R, with derived easting/northing:

| Field | Value |
|-------|-------|
| `utmZone` | `28` |
| `mgrsZone` | `R` |
| `utmEasting` | `316350` |
| `utmNorthing` | `3137250` |
| `utmDatum` | `WGS84` |

`Locality.text3` stays `CS 163,372`. Projection-parameter columns on `LocalityDetail` (`utmFalseEasting`, `utmScale`, origin latitude/longitude) are left empty; standard UTM is identified by zone and datum.

### `text4` — audit JSON

Machine-readable dump of MUSIT ids, verbatim strings, KP vs derived stored decimals, full UTM axes, flags, and `mapping_version`. Hidden in the UI; used for QA and remigration.

## What we deliberately do **not** do

- Put UTM easting/northing into `lat1text` / `long1text` (those are for WGS text entry).
- Put MGRS into `lat2text` or split grids across lat/long text fields.
- Prefix zone and band onto the verbatim grid in `text3`.
- Store UTM as GeoJSON on `Locality.text5`.
- Set `latLongType` to MUSIT terms like `MGRS` / `UTM` (Specify expects Point / Line / Rectangle).
- Map incomplete `latitude_h` / `longitude_h` into Specify’s second point.
- Convert grids or DMS to WGS during migration.

## UI surfaces (`specify7-config`)

| Form | Shows |
|------|--------|
| **Locality** | Where → WGS → MGRS Grid ref → `LocalityDetail` (Zone + Band + UTM) → Elevation → … |
| **LocalitySubForm** | Same grouping in compact form |
| **LocalityDetail** | Zone (`utmZone`) + Band (`mgrsZone`) + UTM datum/easting/northing |
| **CollectingEventSub** (on Collection Object) | WGS → MGRS Grid ref → `LocalityDetail` → flags |

Schema labels / UIFormatters: `GridRefMGRS`, `UTMZone`, `MGRSBand`, `UTMDatum`, `UTMEasting`, `UTMNorthing`.
| **CollectingEvent** | Same coordinate fields as the subform |

Schema labels live in `specify7-config` (`Locality.text3` = Grid ref. (MGRS), `LocalityDetail.mgrsZone` = Zone and band). Regex validation uses UIFormatter `GridRefMGRS` (`specli formatter`).

**Note:** Specify 7 only collapses relationship subviews (`initialize="collapse=true"`), not same-table field groups. Advanced fields are therefore demoted to a trailing section; GeoCoordDetails is collapsed by default.

## Related docs

- Inventory of Oracle place/coordinate columns: [Oracle botany datasets — locality / coordinates](oracle_botany_datasets.md#3-locality-geography-coordinates)
- Geography tree (admin names): [Migration strategy](migration_strategy.md) and `flows/lib/oracle_geography_admin.py`
- Forms / schema deploy: [Specify forms & schema git sync](specify_forms_git_sync.md)
