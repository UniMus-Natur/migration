---
layout: default
title: Oracle botany datasets (MUSIT & USD)
nav_order: 11
---

# Oracle botany datasets (MUSIT & USD)

This page documents how **botany-related “datasets”** appear in Oracle **production**: the unified MUSIT layer (`MUSIT_BOTANIKK_FELLES`), **legacy USD** per-museum botany schemas, **Darwin Core–style views** (`V_DARWINCORE`), **vascular (karplanter)** subsets, and other botany-related schemas. It complements the broader [Oracle schema overview](oracle_schema_overview.md).

## What is **USD** vs **MUSIT**? (one database, two layers)

They are **not** two different databases you pick between—they are **different Oracle users (schemas)** inside the **same** Oracle instance MUSIT uses today.

| Name | Meaning | Role today |
|------|-----------|------------|
| **USD** | *Universitetsmuseenes Samlingsdata* — the **older** per-museum collection applications and their table design (`FUNNETIKETT`, `EKSEMPLAR`, … in `USD_BOTANIKK_*`, plus shared `USD_FELLES`, `USD_METADATA`, …). | **Legacy but still real data**: specimens and shared resources often **originated** here. MUSIT did not throw that away; the newer model **wraps** and **imports** from it (see `LEGACY_EVENT` and migration notes in the [schema overview](oracle_schema_overview.md)). Lots of `USD_*` objects are **noise for Specify** (translations, old backups), but the **core botany USD schemas are not a dead end** for historical specimen rows—they are the row-level source behind much of what MUSIT shows. |
| **MUSIT** | The **newer** application stack: event-sourced `MUSEUM_OBJECT` / `EVENT` / … in schemas like `MUSIT_BOTANIKK_FELLES`. | **Primary target for mapping to Specify** for current collection logic. |

So: **USD is not “crap” in the sense of unused Oracle**—it is the **old CMS data model** still sitting beside MUSIT. For your work, **prefer `MUSIT_*` for structure**, and touch **`USD_*` when you need legacy fields, DwC views (`V_DARWINCORE`), or shared tables** (media, users, taxon registry).

## MUSIT schemas in Oracle (full `MUSIT%` list)

In Oracle, a **schema** is a **user**. Names below are **`USERNAME` from `ALL_USERS`** where `username LIKE 'MUSIT%'`, ordered alphabetically (prod snapshot; your account only sees users Oracle exposes to you—re-run the query if you need an authoritative list):

| Schema name |
|-------------|
| `MUSITDMU` |
| `MUSIT_ADB_IMPORT` |
| `MUSIT_ADB_USER_SKRIV` |
| `MUSIT_BOTANIKK_ALGE` |
| `MUSIT_BOTANIKK_ALGE_FOTO` |
| `MUSIT_BOTANIKK_ALGE_HIS` |
| `MUSIT_BOTANIKK_FELLES` |
| `MUSIT_BOTANIKK_FELLES_FOTO` |
| `MUSIT_BOTANIKK_FELLES_HIS` |
| `MUSIT_BOTANIKK_LAV` |
| `MUSIT_BOTANIKK_LAV_FOTO` |
| `MUSIT_BOTANIKK_LAV_HIS` |
| `MUSIT_BOTANIKK_LOAN` |
| `MUSIT_BOTANIKK_LOAN_HIS` |
| `MUSIT_BOTANIKK_MOSE` |
| `MUSIT_BOTANIKK_MOSE_FOTO` |
| `MUSIT_BOTANIKK_MOSE_HIS` |
| `MUSIT_BOTANIKK_SOPP` |
| `MUSIT_BOTANIKK_SOPP_FOTO` |
| `MUSIT_BOTANIKK_SOPP_HIS` |
| `MUSIT_COORDINATE` |
| `MUSIT_COORDINATE_HIS` |
| `MUSIT_COORDINATE_UTILS` |
| `MUSIT_DARWIN_CORE_IMPORT` |
| `MUSIT_HERB_IMPORT` |
| `MUSIT_MAPPING` |
| `MUSIT_MAPPING_READ` |
| `MUSIT_MEDIA_USER_OPPLASTING` |
| `MUSIT_NATHIST_COORDINATES` |
| `MUSIT_NATHIST_DUMMY` |
| `MUSIT_NATHIST_FELLES` |
| `MUSIT_NATHIST_GIS` |
| `MUSIT_NAT_GIS_USER` |
| `MUSIT_PLACENAMES` |
| `MUSIT_PUBLIC_POPULATE_USER` |
| `MUSIT_PUBLIC_USER_LES` |
| `MUSIT_ROLE_ADMIN` |
| `MUSIT_TEST_1` |
| `MUSIT_ZOOLOGI_ENTOMOLOGI` |
| `MUSIT_ZOOLOGI_ENTOMOLOGI_FOTO` |
| `MUSIT_ZOOLOGI_ENTOMOLOGI_HIS` |

**How to read the names**

- **`MUSIT_BOTANIKK_FELLES`** — shared **botany** “core” MUSIT model (the one migration focuses on first).
- **`MUSIT_BOTANIKK_{MOSE,LAV,SOPP,ALGE}`** (+ `*_FOTO`, `*_HIS`) — **discipline- or workflow-specific** botany satellites (moss, lichen, fungi, algae), history, photos—not duplicate “random” DBs; they support those domains.
- **`MUSIT_ZOOLOGI_ENTOMOLOGI`*** — entomology MUSIT stack (same `_FOTO` / `_HIS` pattern).
- **`MUSIT_NATHIST_*`, `MUSIT_COORDINATE*`, `MUSIT_PLACENAMES`, `MUSIT_DARWIN_CORE_IMPORT`, `MUSIT_HERB_IMPORT`, …** — **infrastructure / GIS / import / public read** helpers, not specimen “core” like `FELLES`.
- **`MUSIT_ROLE_ADMIN`**, **`MUSIT_*USER*`**, **`MUSIT_MAPPING*`** — **security, mapping, service accounts**.

Refresh:

```sql
SELECT username FROM all_users WHERE username LIKE 'MUSIT%' ORDER BY 1
```

## Geolocation in Oracle (MUSIT hub, USD legacy, shared registers) {: #geolocation-oracle-musit-usd}

This section complements [Collecting event, locality, geography](#collecting-event-locality-geography) below and [Step 1.2 — Geography in the migration strategy](migration_strategy.md#step-12--geography). It answers: **where is “location” shared vs siloed**, and **which identifiers are safe to treat as global**.

Exploration path used here: `source scripts/port-forward.sh` (Oracle tunnel) and the `oracle_sql` helper from that script (see `scripts/oracle_sql.py`).

### What is not global (critical for ETL)

- **`KOORDINATE_PLACE_ID` is unique only within one owner schema.** The same integer can appear in `MUSIT_BOTANIKK_FELLES.KOORDINATE_PLACE` and `MUSIT_ZOOLOGI_ENTOMOLOGI.KOORDINATE_PLACE` with **different** coordinate payloads. Example from **Oracle PROD, 2026-04-15**: id `101937` — botany row had `COORDINATE_STRING = 'NQ 35,40'` with null decimal lat/long; the entomology row for the same id had a decimal degree string and populated `LATITUDE_L` / `LONGITUDE_L`. Always treat specimen coordinates as **`(<schema>, KOORDINATE_PLACE_ID)`**, never as a single global key.
- **`PLACE_ID` is per discipline schema** (`MUSIT_BOTANIKK_FELLES.PLACE` vs `MUSIT_ZOOLOGI_ENTOMOLOGI.PLACE`). Do not merge or deduplicate places across schemas by numeric id alone.
- **USD** geographic lookups (`ADMINISTRATIVTSTED`, `KOORDINATSETT`, `GEOREG`, …) live **per museum schema** (`USD_BOTANIKK_TRONDHEIM`, `USD_BOTANIKK_TROMSO`, …). Visibility depends on grants: use `ALL_TABLES` / `ALL_TAB_COLUMNS` to see what your Oracle user can query.

### What is shared across MUSIT applications

- **`MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION`** — a small, shared vocabulary of biogeographic region names. **`MUSIT_BOTANIKK_FELLES.PLACE_BIO_GEOGRAFISK_REGION`** links `PLACE_ID` to **`BIO_GEOGRAFISK_REGION_ID`**, which matches **`MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION.BIO_GEO_REG_ID`** (see `schemas/schema.dbml` refs). That is the main **cross-schema shared register** for “which biogeographic region” on a MUSIT place.

### MUSIT botany “place stack” (`MUSIT_BOTANIKK_FELLES`)

Collecting geography is centred on **`PLACE`** (`PLACE_ID`, `PLACE_NAME_AGG`). Facets hang off junction tables:

| Table | Role |
|-------|------|
| **`PLACE_LOCALITY_PLACE`** | Free-text locality via **`LOCALITY_PLACE`** (`LOCALITY`) |
| **`PLACE_HIERACHICAL_PLACE`** | Administrative / hierarchical names via **`HIERARCHICAL_PLACE_OLD`** (`HIERARCH_PLACE_ID`, `HIERACHICAL_PLACENAME`, `HIERACHICAL_TYPE`, parent `PLACE_ID_PARTOF`) |
| **`PLACE_INDEXED_LOCALITY`** | Indexed / gazetteer-style **`INDEXED_LOCALITY`** rows |
| **`KOORDINATE_PLACE_PLACE`** | Coordinates in **`KOORDINATE_PLACE`** (verbatim strings, UTM/MGRS fields, lat/long low/high, precision, sources, …) and optional **`DERIVED_COORDINATES`** |
| **`PLACE_BIO_GEOGRAFISK_REGION`** | Link to **`MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION`** |
| **`PLACE_ECOLOGY_PLACE`**, **`PLACE_STORING_PLACE`**, … | Ecology text, storage site, etc. |

**`ADMINISTRATIVE_PLACE`** and **`PLACE_ADMINISTRATIVE_PLACE`** model a dedicated admin hierarchy (`ADMPLACENAME`, `ADMPLACE_TYPE`, optional `KOORDINATE_PLACE_ID` on the admin node). In a **2026-04-15** PROD check with the migration reporting account, **`SELECT COUNT(*)` on both tables returned `0`**, while **`PLACE_HIERACHICAL_PLACE`** had ~1.83M rows joining **`PLACE`** to **`HIERARCHICAL_PLACE_OLD`**. Re-run the counts on your credentials before locking import logic; if your environment matches, **use `HIERARCHICAL_PLACE_OLD` (and `HIERACHICAL_TYPE` → `TYPES`) as the live admin-name source** for botany, in addition to USD **`ADMINISTRATIVTSTED` / `GEOREG`** where you have access. Empty `ADMINISTRATIVE_PLACE` may also reflect VPD or a retired path—confirm with a full-privilege account if counts disagree.

Staging / import helpers such as **`BERGEN_ADM_PLACE`**, **`TROMSO_ADM_PLACE`**, **`TEMP_ADM_PLACE`** also appear under `MUSIT_BOTANIKK_FELLES` for museum-specific admin place work.

### Entomology (`MUSIT_ZOOLOGI_ENTOMOLOGI`)

The same **hub-and-spoke** idea applies (`PLACE`, `LOCALITY_PLACE`, `KOORDINATE_PLACE`, …), but administrative links use **`ZZPLACE_ADMINISTRATIVE_PLACE`** (not `PLACE_ADMINISTRATIVE_PLACE`), and there are extra domain tables (`HOST_PLACE`, `STATION_PLACE`, `REGION_PLACE`, `EIS_PLACE`, …). Again: **resolve every place FK in the schema that owns the specimen.**

### USD legacy (per museum)

Core pattern: **`FUNNETIKETT`** + **`KOORDINATSETT`** / **`ADMINISTRATIVTSTED`** / **`GEOREG`** (where present). Not every USD botany user has a **`GEOREG`** table (in one prod metadata query, **`GEOREG`** appeared for `USD_BOTANIKK_TRONDHEIM` but not for Tromsø/Bergen/Svalbard under the same account’s `ALL_TABLES`).

### Utility schemas (not the specimen locality store)

- **`MUSIT_COORDINATE`** — conversion / test helpers (e.g. `USER_TEST_LOG`, `Z_SONE_BAND_MGRS` in `schemas/schema.dbml`), not the authoritative per-specimen coordinate row.
- **`MUSIT_NATHIST_FELLES`** — shared biogeographic regions; **`HIERARCHICAL_PLACE_BIO_GEO_REG`** links hierarchical place ids to those regions for workflows that use that join path.

### Prod snapshot (approximate, 2026-04-15)

Counts from live `oracle_sql` queries; they drift over time.

| Object | ~Rows |
|--------|------:|
| `MUSIT_BOTANIKK_FELLES.PLACE` | 2.21M |
| `MUSIT_BOTANIKK_FELLES.KOORDINATE_PLACE` | 2.01M |
| `MUSIT_BOTANIKK_FELLES.KOORDINATE_PLACE_PLACE` | 1.92M |
| `MUSIT_BOTANIKK_FELLES.PLACE_LOCALITY_PLACE` | 1.83M |
| `MUSIT_BOTANIKK_FELLES.PLACE_HIERACHICAL_PLACE` | 1.83M |
| `MUSIT_BOTANIKK_FELLES.PLACE_BIO_GEOGRAFISK_REGION` | 1.44M |
| `MUSIT_BOTANIKK_FELLES.HIERARCHICAL_PLACE_OLD` | 5.3k |
| Distinct `HIERACHICAL_PLACE_ID` in `PLACE_HIERACHICAL_PLACE` | 2.2k |
| `MUSIT_BOTANIKK_FELLES.INDEXED_LOCALITY` | 3.3k |
| `MUSIT_BOTANIKK_FELLES.LOCALITY_PLACE` | 1.83M |
| `MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION` | 18 |
| `MUSIT_ZOOLOGI_ENTOMOLOGI.KOORDINATE_PLACE` | 1.82M |
| `USD_BOTANIKK_TRONDHEIM.GEOREG` | 1.1k |
| `USD_BOTANIKK_TRONDHEIM.ADMINISTRATIVTSTED` | 5.1k |
| `MUSIT_BOTANIKK_FELLES.ADMINISTRATIVE_PLACE` | 0 (see caveat) |

### SQL snippets to re-check

Do not terminate statements with `;` when piping into `oracle_sql` (the helper strips one trailing semicolon only).

```sql
SELECT owner, table_name FROM all_tables
 WHERE table_name IN ('GEOREG','ADMINISTRATIVTSTED','ADMINISTRATIVE_PLACE')
 ORDER BY owner, table_name
```

```sql
SELECT COUNT(*) FROM musit_botanikk_felles.administrative_place
```

```sql
SELECT COUNT(*) FROM musit_botanikk_felles.place_hierachical_place
```

```sql
SELECT COUNT(DISTINCT hierachical_place_id) FROM musit_botanikk_felles.place_hierachical_place
```

(Column name **`hierachical_place_id`** matches Oracle spelling in `PLACE_HIERACHICAL_PLACE`.)

### Geography/locality migration scope

Geography and locality records are migrated during per-dataset runs rather than by a dedicated standalone flow. Use schema-qualified Oracle identifiers (`owner + PLACE_ID`, `owner + KOORDINATE_PLACE_ID`) as stable keys and keep writes idempotent (update existing rows when the source record already has a mapped Specify row).

### Source-native Oslo vascular slice (no DwC view)

For source-driven migration, use **`MUSIT_BOTANIKK_FELLES.V_OBJECT_ATTRIBUTES`** as the
selection gate and then join to the normalized MUSIT event/place/taxon tables by `OBJECT_ID`.
`V_OBJECT_ATTRIBUTES` is backed by `OBJECT_ATTRIBUTES` and adds institution/collection via
`pkg_search.get_institutioncode(object_id)` and `pkg_search.get_collectioncode(object_id)`.

Core filter:

```sql
SELECT COUNT(*)
  FROM MUSIT_BOTANIKK_FELLES.v_object_attributes
 WHERE institutioncode = 'O'
   AND collectioncode = 'V'
```

Observed count in PROD snapshot used during exploration: **1,149,083** rows.

#### Connected data verified for this slice

For sampled `OBJECT_ID` rows in this filter, the following joins worked and returned usable data:

- `OBJECT_ATTRIBUTES` + `MUSEUM_OBJECT`:
  - workflow/status fields (`IS_REG`, `IS_APPROVED`, `OBJECT_WITHHELD`, `OBJECT_STATE`)
  - identifiers (`IDENTIFIER_STRING`, `IDENTIFIER_NUM`)
  - media pointer (`MEDIAGRUPPE_ENHETS_ID`, often null)
- `EVENT_MUSEUM_OBJECT` + `EVENT` + `COLLECTING_EVENT`:
  - multiple event types per object (collecting + determination/history events)
- `PLACE_EVENT_ROLE` + `PLACE_LOCALITY_PLACE` + `LOCALITY_PLACE`:
  - place/locality text for collecting events
- `KOORDINATE_PLACE_PLACE` + `KOORDINATE_PLACE`:
  - coordinate string / datum / decimal lat-lon (coverage varies)
- `CLASSIFICATION_EVENT` + `CLASSIFICATION_TERM` + `CLASSTERM_LATIN_NAME` + `LATIN_NAMES`:
  - determination history + taxon identifiers (`NHM_TAXON_ID`, `ADB_LATIN_NAME_ID`)
- `EVENT_ROLE_PERSON_NAME`:
  - person-role links present for sampled records (while `EVENT_ROLE_ACTOR` may be empty)

Reference query skeleton (one object envelope with collecting event + place/locality + taxonomy):

```sql
SELECT
  voa.object_id,
  oa.uuid,
  mo.identifier_string,
  oa.is_reg,
  oa.is_approved,
  ce.event_id AS collecting_event_id,
  ce.collectiontype_id,
  por.place_id,
  lp.locality,
  kp.coordinate_string,
  kp.latitude_l,
  kp.longitude_l,
  cte.classification_type_id,
  ct.classterm,
  ln.latin_name,
  ln.nhm_taxon_id,
  ln.adb_latin_name_id
FROM MUSIT_BOTANIKK_FELLES.v_object_attributes voa
JOIN MUSIT_BOTANIKK_FELLES.object_attributes oa ON oa.object_id = voa.object_id
JOIN MUSIT_BOTANIKK_FELLES.museum_object mo ON mo.object_id = voa.object_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.event_museum_object emo ON emo.object_id = voa.object_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.collecting_event ce ON ce.event_id = emo.event_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.place_event_role por ON por.event_id = ce.event_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.place_locality_place plp ON plp.place_id = por.place_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.locality_place lp ON lp.locality_place_id = plp.locality_place_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.koordinate_place_place kpp ON kpp.place_id = por.place_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.koordinate_place kp ON kp.koordinate_place_id = kpp.koordinate_place_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.classification_event cte ON cte.event_id = emo.event_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.classification_term ct ON ct.class_term_id = cte.class_term_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.classterm_latin_name ctl ON ctl.classterm_id = ct.class_term_id
LEFT JOIN MUSIT_BOTANIKK_FELLES.latin_names ln ON ln.latin_name_id = ctl.latin_name_id
WHERE voa.institutioncode = 'O'
  AND voa.collectioncode = 'V'
  AND voa.object_id = :object_id
```

Notes:

- `EVENT_MUSEUM_OBJECT` is one-to-many from object to events, so object-level queries should
  either aggregate per event type or select a specific event class (collecting, classification, etc.).
- `UUID` coverage is partial in this source slice (many rows have null UUID), so `OBJECT_ID` remains
  the most stable internal key for source-native migration.

## Storage model (MUSIT botany)

There is **no** dedicated `DATASET` table in `MUSIT_BOTANIKK_FELLES`. Logical grouping uses columns on **`OBJECT_ATTRIBUTES`**, keyed by **`OBJECT_ID`** to **`MUSEUM_OBJECT`** (same pattern as in the schema overview: central object + attributes row).

| Column | Type (approx.) | Role |
|--------|----------------|------|
| `OBJECT_ATTRIBUTES.DATASET` | `VARCHAR2(512)` | Optional label for a named dataset / collection group (expedition, herbarium subset, etc.). |
| `OBJECT_ATTRIBUTES.PROJECT_NAME` | `VARCHAR2(512)` | Optional project or expedition name; used much more often than `DATASET` for free-text grouping. |

Other columns on `OBJECT_ATTRIBUTES` (registration dates, UUID, workflow flags, etc.) are documented at table level in [Oracle schema overview — Family 2](oracle_schema_overview.md#family-2--musit-schemas-event-sourced-structured).

## Scale (prod snapshot)

Figures below come from **one** live query against Oracle PROD; counts drift as data changes.

| Measure | Approximate value |
|---------|-------------------|
| Rows in `MUSIT_BOTANIKK_FELLES.OBJECT_ATTRIBUTES` | ~2.0M |
| Distinct **non-empty** `DATASET` values | **12** |
| Rows with null or blank `DATASET` | ~1.98M |
| Distinct **non-empty** `PROJECT_NAME` values | **~1.5k** |

Interpretation: **`DATASET` is sparsely populated**; most botany objects in MUSIT are **not** partitioned by that field. **`PROJECT_NAME`** carries more of the human-readable “which project / expedition” dimension.

### Example distinct `DATASET` values (prod)

These names appeared as the full distinct set when `DATASET` was non-null (again, subject to change in live DB):

- Macaronesia  
- Berg California, Berg Australia, Berg Macaronesia  
- Typer  
- Tristan da Cunha, Burma, Tirich Mir  
- Herbarium Antarcticum, Bhanu  
- Plus occasional one-offs (e.g. test or historical label rows)

Re-run the SQL below to refresh the list and counts.

## Legacy USD botany (“datasets” as schemas)

Before / beside the unified MUSIT layer, **per-museum botany** lived in separate Oracle **schemas** (each acts like a siloed “dataset” in infrastructure terms):

| Schema | Code (informal) | ~Tables (prod) |
|--------|-----------------|----------------|
| `USD_BOTANIKK_TRONDHEIM` | TRH | 81 |
| `USD_BOTANIKK_TROMSO` | TMS | 78 |
| `USD_BOTANIKK_BERGEN` | BRG | 66 |
| `USD_BOTANIKK_SVALBARD` | SVA | 73 |

These four are the **main operational USD herbarium** schemas referenced in the migration docs. Oracle **also** defines additional botany-related users (backups, tests, admin, organism-specific MUSIT apps, etc.). The list below comes from `ALL_USERS` (prod snapshot; your account may not have `SELECT` on all of them):

| Pattern | Examples (not exhaustive) |
|---------|----------------------------|
| **MUSIT botany satellites** | `MUSIT_BOTANIKK_MOSE`, `MUSIT_BOTANIKK_LAV`, `MUSIT_BOTANIKK_SOPP`, `MUSIT_BOTANIKK_ALGE`, plus matching `*_FOTO`, `*_HIS`, `MUSIT_BOTANIKK_LOAN` |
| **USD extras** | `USD_BOTANIKK_B1` … `B5`, `USD_BOTANIKK_REGADM`, `USD_BOTANIKK_SOPP`, `USD_BOTANIKK_TEST`, `USD_BOTANIKK_TESTBRUKER`, `USD_BOTANIKK_TRHBACK3`, `USD_BOTANIKK_TRONDHEIMBACK` |
| **DiGIR legacy** | `DIGIR_MUSIT` (e.g. view `V_DIGIR_DARWIN`) |

```sql
SELECT username FROM all_users
 WHERE username LIKE 'MUSIT%BOTAN%' OR username LIKE 'USD%BOTAN%'
 ORDER BY 1
```

See [Oracle schema overview — Family 1](oracle_schema_overview.md#family-1--usd-schemas-legacy-per-museum).

### IPT `main` vs Oracle (your DwC-shaped SQL)

If you run SQL against **`FROM main`** with columns like `InstitutionCode`, `CollectionCode`, `CatalogNumber`, `ScientificName`, …, that is almost certainly **not** Oracle: IPT’s publishing stack often uses an **SQLite** (or similar) database where the export table is literally named **`main`** (or the IPT resource DB). Oracle has **no** standard `main` table for that purpose.

The **closest Oracle equivalent** in the USD botany schemas is the view **`V_DARWINCORE`** (English column names) and **`V_DARWINCORE_NORSK`** (Norwegian labels). **`TAXA_BESTEMMELSE_DARWINCORE`** also exists per museum for taxon/determination–oriented DwC-style exports.

**`V_DARWINCORE` exists** on `USD_BOTANIKK_TRONDHEIM`, `USD_BOTANIKK_TROMSO`, and `USD_BOTANIKK_BERGEN`. It was **not** present under `USD_BOTANIKK_SVALBARD` in the same metadata query (Svalbard still has many `*_KARPLANTER` / `TAXA_*` views—check `ALL_VIEWS` there for current exports).

Example columns on **`USD_BOTANIKK_TROMSO.V_DARWINCORE`** (compare to your IPT query):

| Oracle `V_DARWINCORE` | Typical IPT / DwC name you used |
|----------------------|----------------------------------|
| `INSTITUTIONCODE` | `InstitutionCode` |
| `COLLECTIONCODE` | `CollectionCode` |
| `CATALOGNUMBER` | `CatalogNumber` |
| `SCIENTIFICNAME`, `KINGDOM`, `PHYLUM`, … | same idea |
| `KLASSENAVN` | `Class` |
| `ORDENSNAVN` | `Order` |
| `PHOTOURL` | often mapped to `associatedMedia` / `URL` |
| `COLLECTORNUMBER` | related to `FieldNumber` / collector fields (verify per resource) |

Inspect the full projection:

```sql
SELECT column_name FROM all_tab_columns
 WHERE owner = 'USD_BOTANIKK_TROMSO' AND table_name = 'V_DARWINCORE'
 ORDER BY column_id
```

The view text starts from **`FUNNETIKETT`** / specimen logic (e.g. `reg_nr` as catalog number); use `ALL_VIEWS.TEXT` (or your SQL client’s “view SQL”) for the exact join graph.

### Vascular plants (**karplanter**) in USD

Norwegian **karplanter** = vascular plants. In USD botany, vascular material is **not** always the same as “all rows in `FUNNETIKETT`”:

- **Tromsø, Bergen, Svalbard** expose views such as **`FUNNETIKETT_KARPLANTER`**, **`EKSEMPLAR_KARPLANTER`**, **`ETIKETT_KARPLANTER`**, **`BESTEMMELSE_KARPLANTER`**, **`TAXA_KARPLANTER`**, etc. These are the supported way to stay on **vascular** subsets (alongside parallel `*_MOSE`, `*_LAV`, `*_SOPP`, … views where they exist).
- **Trondheim (`USD_BOTANIKK_TRONDHEIM`)** is different at **label** level: every `FUNNETIKETT` row appears in **`FUNNETIKETT_MOSE`** or **`FUNNETIKETT_LAV`** (moss + lichen; counts sum to the full `FUNNETIKETT` total). There is **no** `FUNNETIKETT_KARPLANTER` object in that schema. For **vascular** material tied to Trondheim, expect it in **another museum’s USD schema** and/or in **`MUSIT_BOTANIKK_FELLES`**, not in `USD_BOTANIKK_TRONDHEIM`’s moss/lichen USD split.

**Example — vascular-like rows from the DwC view (Tromsø):** restrict `V_DARWINCORE` to labels that appear in **`FUNNETIKETT_KARPLANTER`** (join on catalog number / `FUNNETIKETT.REG_NR` is how one successful count was built; confirm edge cases such as leading zeros):

```sql
SELECT v.*
  FROM usd_botanikk_tromso.v_darwincore v
 WHERE EXISTS (
   SELECT 1
     FROM usd_botanikk_tromso.funnetikett_karplanter k
     JOIN usd_botanikk_tromso.funnetikett f ON f.etikett_id = k.etikett_id
    WHERE TO_CHAR(f.reg_nr) = TO_CHAR(v.catalognumber)
 )
```

Adjust owner literals for **Bergen** or **Svalbard** as needed.

### MUSIT vascular herbarium in Oracle (`DIGIR_MUSIT` / `V_DC_*_VASCULAR`)

The **curated vascular-plant Darwin Core export** used operationally (e.g. IPT-style SQL) is **`DIGIR_MUSIT.V_DC_O_VASCULAR`** and siblings—not `MUSIT_BOTANIKK_FELLES.V_*` alone.

**What the view actually is**

`ALL_VIEWS` text for `V_DC_O_VASCULAR` (and `V_DC_TRH_VASCULAR`, `V_DC_TROM_VASCULAR`, …) is the same pattern:

- **`FROM dc_vascular_felles t WHERE t.institutioncode = '<code>'`**
- Plus columns from `t`, and **`pkg_tools.get_aggregated_elevation_VASC(t.object_id)`** for aggregated verbatim elevation (package in **`DIGIR_MUSIT`**).

So the **physical row store** behind the export is the object **`DC_VASCULAR_FELLES`** in schema **`DIGIR_MUSIT`**: one table (or materialized object) holding **pre-flattened DwC fields** for **vascular** herbarium rows, partitioned logically by **`INSTITUTIONCODE`**. Each public view is a thin filter:

| View | `INSTITUTIONCODE` filter |
|------|---------------------------|
| `DIGIR_MUSIT.V_DC_O_VASCULAR` | `O` |
| `DIGIR_MUSIT.V_DC_TRH_VASCULAR` | `TRH` |
| `DIGIR_MUSIT.V_DC_TROM_VASCULAR` | `TROM` |
| `DIGIR_MUSIT.V_DC_BG_VASCULAR` | `BG` |
| `DIGIR_MUSIT.V_DC_SVG_VASCULAR` | `SVG` |
| `DIGIR_MUSIT.V_DC_KMN_VASCULAR` | `KMN` |

**Linking into `MUSIT_BOTANIKK_FELLES` for “more than the view”**

The DwC view does **not** expose `OBJECT_ID` in `ALL_TAB_COLUMNS`, but the underlying `dc_vascular_felles` row **does** supply `t.object_id` inside the view definition (for the elevation package). Ways to reach the live MUSIT model:

1. **UUID (works with typical app grants)** — the view includes **`UUID`**. Join to **`MUSIT_BOTANIKK_FELLES.OBJECT_ATTRIBUTES`** on `LOWER(TRIM(oa.uuid)) = LOWER(TRIM(v.uuid))`, then to **`MUSEUM_OBJECT`** on `oa.object_id = mo.object_id`. From there you can walk **`EVENT_MUSEUM_OBJECT`**, **`PLACE`**, **`CLASSIFICATION_EVENT`**, etc.

2. **Direct `OBJECT_ID`** — if your DB user can `SELECT` from **`DIGIR_MUSIT.DC_VASCULAR_FELLES`**, use `object_id` and join straight to **`MUSIT_BOTANIKK_FELLES.MUSEUM_OBJECT`**. If you get `ORA-00942` on the base table, ask a DBA for **`SELECT` on `DIGIR_MUSIT.DC_VASCULAR_FELLES`** (or for the view definition / ETL source job that fills it).

**Example join shell**

```sql
SELECT v.catalognumber, v.scientificname, mo.object_id, mo.identifier_string
  FROM digir_musit.v_dc_o_vascular v
  JOIN musit_botanikk_felles.object_attributes oa
    ON LOWER(TRIM(oa.uuid)) = LOWER(TRIM(v.uuid))
  JOIN musit_botanikk_felles.museum_object mo ON mo.object_id = oa.object_id
 WHERE v.uuid IS NOT NULL
 FETCH FIRST 20 ROWS ONLY
```

For **MUSIT**-side reporting beyond DwC, prefer joins from **`V_DC_*_VASCULAR`** or **`DC_VASCULAR_FELLES`** into **`MUSIT_BOTANIKK_FELLES`** as above; the older `MUSIT_BOTANIKK_FELLES.V_*` views are a different read-model family (search / admin UI), not the same as this DiGIR vascular slice.

### Core specimen tables (row counts, prod snapshot)

Each schema follows the same **USD botany** pattern: **`FUNNETIKETT`** (label / gathering record), **`EKSEMPLAR`** (physical specimen), **`BESTEMMELSE`** (determinations), plus geography, persons, media, etc. Approximate **row counts** from one live query:

| Code | `FUNNETIKETT` | `EKSEMPLAR` | `BESTEMMELSE` |
|------|---------------|-------------|----------------|
| TRH | ~174k | ~215k | ~258k |
| TMS | ~253k | ~257k | ~304k |
| BRG | ~193k | ~193k | ~229k |
| SVA | ~25k | ~36k | ~38k |

A **migration “dataset”** can be defined as coarse as **one entire schema** (export everything for that herbarium’s USD botany), or finer-grained using the dimensions below.

### What you can extract (logical dataset dimensions)

These are **not** separate tables named “dataset”; they are **columns and foreign keys** you can `GROUP BY` or filter on when splitting exports.

1. **Museum / schema (always)**  
   The schema name is the strongest boundary: TRH, TMS, BRG, SVA are independent USD installations.

2. **`FUNNETIKETT.PROSJEKT_NAVN` (free text)**  
   Optional project or campaign label on the **find label** row. Cardinality **varies a lot by museum** (examples from prod):
   - **TRH:** almost all rows blank; a single bulk import label dominated non-blank rows (e.g. “Holienimport fra NLD” on ~13k rows).
   - **TMS:** only a **few** distinct values (e.g. digitization / facsimile-style names such as “LavFaksimilier”, “MoseKarasjok”, “SoppFaksimilier”).
   - **BRG:** **five** distinct non-empty values, all **Foto***-prefixed (large photo-digitization batches: e.g. FotoFlisa, FotoKarasjok, …).
   - **SVA:** **more** distinct values (~15 in the snapshot), often **expedition / author / locality** phrasing (Svalbard field campaigns, thesis data collections, etc.).

   Use this when you need **human-named slices** inside one museum.

3. **`FUNNETIKETT.ORGANISME_TYPE` (coarse taxon habit)**  
   Example on **TRH:** mostly **Mose** vs **Lav** (two large populations). Useful for **taxonomic group** splits within a schema, not fine species-level datasets.

4. **`FUNNETIKETT.FUNNTYPE_ID` → `FUNNTYPE` (lookup)**  
   Intended specimen / record **type** classification. The **`FUNNTYPE` lookup table can be empty** in a given schema while `FUNNTYPE_ID` is still populated (e.g. NULL vs a small set of IDs). Treat as **optional** metadata; verify `SELECT COUNT(*) FROM <schema>.FUNNTYPE` before relying on labels.

5. **`FUNNETIKETT.INNSAMLINGSMETODE_ID` → `INNSAMLINGSMETODE`**  
   Collecting **method** (`INNSAMLINGSMETODE.INNSAMLINGSMETODE` is the name column). In **TRH** prod snapshot, **`INNSAMLINGSMETODE_ID` was 100% NULL** on `FUNNETIKETT`, so that dimension **does not slice TRH** data today; other museums may populate it—check per schema.

6. **`EKSEMPLAR` + `EKSEMPLAR_TYPE_ID`**  
   Physical specimen **kinds** (via type lookup). Good for **excluding non-sheet material** or grouping by preparation type when the lookups are maintained.

7. **Linked subsystems (same schema)**  
   Determinations (`BESTEMMELSE` + taxon tables), localities (`GEOREG`, `KOORDINATSETT`, …), collectors (`LEGSAMLER`, `PERSONER`), attachments (`BILDE` flags / media paths)—all can define **technical** extract slices (e.g. “records with coordinates”, “type specimens only”) using the same core keys (`ETIKETT_ID`, etc.). See the [Family 1 table list](oracle_schema_overview.md#family-1--usd-schemas-legacy-per-museum).

### SQL recipes (USD)

Use your normal Oracle access path (for example `oracle_sql` after [port forwarding and credentials](dev_container.md#4-database-proxies-port-forwarding)).

**Do not** end statements with `;` when using `python-oracledb` `execute()` (including `oracle_sql`): Oracle returns `ORA-00933`. The `oracle_sql` helper strips **one** trailing semicolon for convenience.

### List all non-empty `DATASET` values and object counts

```sql
SELECT TRIM(dataset) AS dataset, COUNT(*) AS object_count
  FROM musit_botanikk_felles.object_attributes
 WHERE dataset IS NOT NULL
   AND TRIM(dataset) IS NOT NULL
 GROUP BY TRIM(dataset)
 ORDER BY dataset
```

### List `PROJECT_NAME` values (e.g. top by volume)

```sql
SELECT TRIM(project_name) AS project_name, COUNT(*) AS object_count
  FROM musit_botanikk_felles.object_attributes
 WHERE project_name IS NOT NULL
   AND TRIM(project_name) IS NOT NULL
 GROUP BY TRIM(project_name)
 ORDER BY object_count DESC
 FETCH FIRST 50 ROWS ONLY
```

### Count objects with no `DATASET` label

```sql
SELECT COUNT(*) AS objects_without_dataset
  FROM musit_botanikk_felles.object_attributes
 WHERE dataset IS NULL OR TRIM(dataset) IS NULL
```

### USD: list non-empty `PROSJEKT_NAVN` for one museum schema

Replace the owner literal with `USD_BOTANIKK_TRONDHEIM`, `USD_BOTANIKK_TROMSO`, `USD_BOTANIKK_BERGEN`, or `USD_BOTANIKK_SVALBARD`.

```sql
SELECT TRIM(prosjekt_navn) AS prosjekt, COUNT(*) AS funnetikett_count
  FROM usd_botanikk_trondheim.funnetikett
 WHERE prosjekt_navn IS NOT NULL
   AND TRIM(prosjekt_navn) IS NOT NULL
 GROUP BY TRIM(prosjekt_navn)
 ORDER BY funnetikett_count DESC
```

### USD: `ORGANISME_TYPE` split (example: Trondheim)

```sql
SELECT TRIM(organisme_type) AS organisme_type, COUNT(*) AS cnt
  FROM usd_botanikk_trondheim.funnetikett
 GROUP BY TRIM(organisme_type)
 ORDER BY cnt DESC NULLS LAST
```

## Oracle → Specify (exploration): images and other migratable columns

This section is an **inventory only** (no ETL): where **image / file** data lives, and which Oracle columns are useful when you eventually map **MUSIT / DiGIR** into Specify **`CollectionObject`**, **`CollectingEvent`**, **`Determination`**, **`Locality`**, **`Agent`**, **`Attachment`**, etc.

## Comprehensive source-to-Specify mapping (Oslo vascular, source-native)

This section documents a **migration-safe** approach for `MUSIT_BOTANIKK_FELLES` with filter:

- `V_OBJECT_ATTRIBUTES.INSTITUTIONCODE = 'O'`
- `V_OBJECT_ATTRIBUTES.COLLECTIONCODE = 'V'`

### Retention policy (no data loss)

Because MUSIT will be decommissioned, do **not** rely only on normalized Specify fields.

For every migrated `OBJECT_ID`, persist a raw payload archive (JSON or side tables) containing:

- all selected rows from all connected source tables below, and
- source row identity (`owner`, `table`, PK values), extraction timestamp, and migration run id.

In other words:

- **Mapped to Specify**: fields needed for operational behavior/search/UI.
- **Retain raw**: everything else (even if not currently shown in Specify).
- **Drop**: only technical duplicates when fully derivable from retained data.

Suggested archival key: `MUSIT_BOTANIKK_FELLES:OBJECT_ID:<id>`.

### Connected table graph used

`V_OBJECT_ATTRIBUTES` → `OBJECT_ATTRIBUTES` → `MUSEUM_OBJECT` → `EVENT_MUSEUM_OBJECT` → (`COLLECTING_EVENT`, `CLASSIFICATION_EVENT`, other `EVENT` types) → place/coordinate/taxon/person/document/media tables.

---

### 1) Object identity and workflow

| Oracle table.column | Specify target | Keep / drop | Notes |
|---|---|---|---|
| `V_OBJECT_ATTRIBUTES.OBJECT_ID` | staging map key (`oracle_to_specify_map`) | Keep (raw + key) | Primary source identity for joins. |
| `V_OBJECT_ATTRIBUTES.INSTITUTIONCODE` | `CollectionObject.text*` or migration metadata | Keep | Dataset filter + provenance (`O`). |
| `V_OBJECT_ATTRIBUTES.COLLECTIONCODE` | `Collection.code` routing + metadata | Keep | Dataset filter + provenance (`V`). |
| `OBJECT_ATTRIBUTES.UUID` | `CollectionObject.guid` / `uniqueidentifier` (policy-dependent) | Keep | Partial coverage in source; never use as sole key. |
| `OBJECT_ATTRIBUTES.IS_REG` | `CollectionObject.text*` / quality flag | Keep | Strong indicator for publishability and DwC differences. |
| `OBJECT_ATTRIBUTES.IS_APPROVED` | `CollectionObject.text*` / quality flag | Keep | Strong indicator for publishability and DwC differences. |
| `OBJECT_ATTRIBUTES.OBJECT_WITHHELD` | `CollectionObject.visibility` / custom embargo flag | Keep | Preserve exactly; used in downstream policy decisions. |
| `OBJECT_ATTRIBUTES.OBJECT_STATE` | `CollectionObject.text*` / custom status field | Keep | Keep raw value; can drive QA review. |
| `OBJECT_ATTRIBUTES.REG_DATE` | `CollectionObject.timestampcreated` override or custom date field | Keep | Preserve as source registration timestamp. |
| `OBJECT_ATTRIBUTES.APPROVED_DATE` | custom migrated date field | Keep | Useful for publication timeline / QA. |
| `OBJECT_ATTRIBUTES.LAST_MODIFIED` / users (`REG_USER`, `KORR_USER`, `APPROVE_USER`) | migration provenance fields | Keep | Prefer raw archive + optional text fields. |
| `OBJECT_ATTRIBUTES.DATASET` | `CollectionObject.projectnumber` or remarks/custom field | Keep | Sparse in botany but important in zoology. |
| `OBJECT_ATTRIBUTES.PROJECT_NAME` | `CollectionObject.projectnumber` / remarks/custom field | Keep | High-value grouping context. |
| `OBJECT_ATTRIBUTES.DUBLETTES`, `SAME_SHEET_AS`, `EX_HERB`, `VOUCHER`, `ARTSOBS_NR`, `ANALYSIS_REQUEST` | `CollectionObject.remarks`/custom fields | Keep | High historical/curatorial value; do not discard. |
| `MUSEUM_OBJECT.OBJECT_ID` | staging map key (same id) | Keep | Must be retained alongside OA rows. |
| `MUSEUM_OBJECT.IDENTIFIER_NUM` | `CollectionObject.catalognumber` (formatted per collection policy) | Keep | Usually matches label/catalog identity. |
| `MUSEUM_OBJECT.IDENTIFIER_STRING` | `CollectionObject.catalognumber` / `altcatalognumber` | Keep | Source human-readable identifier (`O-V-...`). |
| `MUSEUM_OBJECT.LONG_NAME` | `CollectionObject.remarks` | Keep | Preserve full descriptive label text. |
| `MUSEUM_OBJECT.MUSEUM_OBJECT_TYPE` | custom mapping or filter | Keep | Map via `TYPES` lookup and retain raw id. |
| `MUSEUM_OBJECT.PARENT_OBJECT_ID` | relationship table (`CollectionRelationship`) or raw | Keep | Needed if object hierarchies should survive. |
| `MUSEUM_OBJECT.SUB_COLLECTION_ID` | collection routing metadata | Keep | Mostly null for sampled O/V but retain always. |
| `MUSEUM_OBJECT.MEDIAGRUPPE_ENHETS_ID` | attachment linkage key | Keep | Critical for media extraction (USD_FELLES). |

---

### 2) Event chain and collecting context

| Oracle table.column | Specify target | Keep / drop | Notes |
|---|---|---|---|
| `EVENT_MUSEUM_OBJECT.EVENT_ID` | link to migrated event rows | Keep | One object → many events. |
| `EVENT_MUSEUM_OBJECT.SEQUENCE_NUMBER` | ordering metadata | Keep | Keep raw for deterministic replay. |
| `EVENT_MUSEUM_OBJECT.PREV_EVENT_FOR_OBJEKT` | event chain metadata | Keep | Needed to reconstruct chronology. |
| `EVENT.EVENT_ID` | `CollectingEvent` / determination linkage key | Keep | Core event identity. |
| `EVENT.EVENT_TYPE` | migration dispatch rule | Keep | Distinguishes collecting vs classification vs other types. |
| `EVENT.EVENTNAME` | `CollectingEvent.remarks` / custom field | Keep | Preserve source event label. |
| `EVENT.TIMESPAN_ID` | join to `TIMESPAN` | Keep | Essential for date precision. |
| `COLLECTING_EVENT.EVENT_ID` | `CollectingEvent` key | Keep | Only for collecting-type events. |
| `COLLECTING_EVENT.COLLECTIONTYPE_ID` | `CollectingEvent.discipline` routing/QA metadata | Keep | In botany O/V often 77/78; keep exact id + label. |
| `COLLECTING_EVENT.LEGNAME_ORIG` | `CollectingEvent.verbatimlocality` or remarks | Keep | Collector-entered original text. |
| `COLLECTING_EVENT.AGG_PERSONNAMES` | `CollectingEvent.text*` or remarks | Keep | Preserve even if structured people exist. |
| `TIMESPAN.FROM_DATE`, `TO_DATE`, `TIME_AS_TEXT`, `UNCERTAIN` | `CollectingEvent.startdate` + precision + verbatim | Keep | Keep all components (structured + verbatim). |

---

### 3) Locality, geography, coordinates

| Oracle table.column | Specify target | Keep / drop | Notes |
|---|---|---|---|
| `PLACE_EVENT_ROLE.EVENT_ID`, `PLACE_ID`, `ROLE_ID` | `CollectingEvent.locality` source linkage + raw role | Keep | Primary event→place connector. |
| `PLACE.PLACE_ID` | migration placemap key | Keep | Stable source place identity. |
| `PLACE.PLACE_NAME_AGG` | `Locality.text1`/remarks fallback | Keep | In current snapshot often null; still retain. |
| `PLACE_LOCALITY_PLACE.LOCALITY_PLACE_ID` | locality text join key | Keep | Bridge to free-text locality. |
| `LOCALITY_PLACE.LOCALITY` | `Locality.localityname` (preferred) | Keep | Core locality text for many records. |
| `PLACE_HIERACHICAL_PLACE.HIERACHICAL_PLACE_ID` | `Locality.geography` derivation input | Keep | Geography mapping chain. |
| `HIERARCHICAL_PLACE_OLD.*` (via hierarchy id) | `Geography` nodes/provenance fields | Keep | Critical for historical admin names. |
| `PLACE_ADMINISTRATIVE_PLACE.*` / `ADMINISTRATIVE_PLACE.*` | optional `Geography` enrichment | Keep | Preserve even if sparsely populated in some envs. |
| `KOORDINATE_PLACE_PLACE.KOORDINATE_PLACE_ID` | coordinate join key | Keep | Place→coordinate bridge. |
| `KOORDINATE_PLACE.COORDINATE_STRING` | `Locality.lat1text/long1text` or `text1` | Keep | Verbatim grid string (often only coordinate available). |
| `KOORDINATE_PLACE.LATITUDE_L`, `LONGITUDE_L` | `Locality.latitude1`, `longitude1` | Keep | Numeric coordinate when valid. |
| `KOORDINATE_PLACE.DATUM` | `Locality.datum` | Keep | Needed for interpretation/transforms. |
| `KOORDINATE_PLACE` UTM/MGRS fields | `Locality` text/custom fields | Keep | Preserve all projected coordinate variants. |
| `DERIVED_COORDINATES.*` | custom coordinate QA fields/raw | Keep | Useful for QA and back-calculation. |
| `PLACE_BIO_GEOGRAFISK_REGION` + `MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION` | `Locality`/`Geography` custom region field | Keep | Important ecological context. |

---

### 4) Determinations, taxon linkage, type info

| Oracle table.column | Specify target | Keep / drop | Notes |
|---|---|---|---|
| `CLASSIFICATION_EVENT.EVENT_ID` | `Determination` source event key | Keep | Attach determinations to object via event chain. |
| `CLASSIFICATION_EVENT.CLASSIFICATION_TYPE_ID` | `Determination` qualifier/provenance | Keep | E.g., original determination, redetermination, confirmation. |
| `CLASSIFICATION_EVENT.CLASS_TERM_ID` | determination concept join | Keep | Bridge to term/name rows. |
| `CLASSIFICATION_TERM.CLASSTERM`, `ENTERED_CLASSTERM`, `VALID_CLASSTERM` | `Determination.remarks` / verbatim identification | Keep | Keep all textual variants. |
| `CLASSTERM_LATIN_NAME.LATIN_NAME_ID` | taxon join key | Keep | Needed for stable taxon mapping. |
| `LATIN_NAMES.LATIN_NAME` | `Taxon.name` / `Determination.text1` fallback | Keep | Core scientific name. |
| `LATIN_NAMES.FULL_NAME`, `FULL_NAME_AUTHOR` | `Taxon.fullname`, `Taxon.author` | Keep | Preferred authoritative formatting. |
| `LATIN_NAMES.NHM_TAXON_ID` | `Taxon.text*` / mapping key | Keep | Stable internal taxon key. |
| `LATIN_NAMES.ADB_LATIN_NAME_ID` | NorTaxa bridge field | Keep | Critical for authority reconciliation. |
| `LATIN_NAMES.IS_VALID`, parent ids, category ids | synonym/accepted logic | Keep | Needed for taxon graph consistency. |
| `TYPIFICATION_EVENT.*`, `TYPE_SPECIMEN.*` | `Determination.istype` + type status fields | Keep | High-value nomenclatural metadata. |

---

### 5) People/agents and role assertions

| Oracle table.column | Specify target | Keep / drop | Notes |
|---|---|---|---|
| `EVENT_ROLE_PERSON_NAME.EVENT_ID`, `ROLE_ID`, `PERSON_NAME_ID` | collector/determiner attribution joins | Keep | In sampled O/V, this carried person-role links. |
| `EVENT_ROLE_ACTOR.EVENT_ID`, `ROLE_ID`, `ACTOR_ID` | collector/determiner attribution joins | Keep | May be sparse by subset; still retain. |
| `PERSON_NAME` fields (`SURNAME`, `GIVEN`, `MIDDLE`, etc.) | `Agent` person fields | Keep | Use agent mapping flow or on-the-fly upsert. |
| `ACTOR` fields (`ACTORNAME`, `ACTOR_TYPE`, `INSTITUTION`, contacts, URL) | `Agent` fields/custom | Keep | Keep full source actor payload for future reconciliation. |
| `MUSEUM_OBJECT_LEGNR_PERSON` (`OBJECT_ID`, `ACTOR_ID`, `LEGNR`) | collector numbering/provenance | Keep | Useful for historical legator notation. |

---

### 6) Notes, documents, attachments, references

| Oracle table.column | Specify target | Keep / drop | Notes |
|---|---|---|---|
| `NOTE.NOTE_TEXT`, `NOTE.LONG_NOTE` | `CollectionObject.remarks` / note attachments | Keep | Do not collapse to one field without raw retention. |
| `MUSEUM_OBJECT_NOTE` links (`OBJECT_ID`, `NOTE_ID`, `TYPE_ID`) | object-level remarks/citations | Keep | Preserve note typing via `TYPES`. |
| `EVENT_NOTE` links (`EVENT_ID`, `NOTE_ID`, `NOTE_TYPE_ID`) | event/determination remarks | Keep | Keep role/type semantics. |
| `REFERENCE_DOCUMENT.DOCUMENT_*` | `ReferenceWork` / attachment | Keep | Includes blob/text/reference pointers. |
| `DOCUMENT_OBJECT`, `EVENT_DOCUMENT` link tables | attach docs to object/event | Keep | Needed to reconstruct context attachments. |
| `USD_FELLES.MEDIAGRUPPE_ENHET.*` | attachment grouping metadata | Keep | Key bridge from object to binary files. |
| `USD_FELLES.MEDIA_FIL.*` (including filenames, mime/type, blob refs) | `Attachment` + `CollectionObjectAttachment` | Keep | Preserve all versions and source file metadata. |
| `USD_FELLES.MEDIA__PATHS.*` | file acquisition pipeline metadata | Keep | Needed to resolve physical file locations. |

---

### 7) Keep/drop policy revision (map all meaningful data)

Policy for this migration is now:

1. **Keep and map everything that has operational or scientific value** into native Specify fields
   where a reasonable target exists (`CollectionObject`, `CollectingEvent`, `Locality`,
   `Determination`, `Taxon`, `Agent`, `Attachment`, and attribute tables).
2. **Do not silently drop the remainder**. Any source fields not mapped to first-pass Specify
   columns must be serialized into a per-object JSON payload stored on `CollectionObject` text
   storage (prefer `CollectionObject.text1`, with overflow strategy to `text2`/`text3` if needed).
3. **Only true drop class**: strict duplicates that are byte-for-byte derivable from retained values
   and add no provenance.

Recommended JSON envelope for `CollectionObject.text1`:

```json
{
  "source": {
    "owner": "MUSIT_BOTANIKK_FELLES",
    "object_id": 12345,
    "dataset": "O-Vascular"
  },
  "unmapped": {
    "MUSEUM_OBJECT": { "...": "..." },
    "OBJECT_ATTRIBUTES": { "...": "..." },
    "PLACE": { "...": "..." },
    "EVENT": { "...": "..." }
  },
  "migration_meta": {
    "exported_at_utc": "2026-04-20T00:00:00Z",
    "mapping_version": "oslo-vascular-v1"
  }
}
```

This keeps Specify operational while preserving no-loss source detail directly with each specimen.

Decision rule summary:

- **Keep + map to native Specify fields**
  - identity and cataloguing (`catalogNumber`, GUID/UUID bridges, project/dataset context);
  - current collecting context (date, locality, geography, coordinates, datum);
  - current taxonomy/determination state (`Determination`, `Taxon`, `iscurrent=true`);
  - agent links and attachment/document links that have first-class targets.
- **Keep + store as JSON on `CollectionObject`**
  - role/event/link-table structures that do not fit cleanly in first-pass schema;
  - extra source columns needed for provenance/auditability but not user-facing day one;
  - all remaining unmapped but meaningful columns from connected source rows.
- **Drop**
  - strict duplicates/aliases that are fully derivable from already retained values;
  - transient technical helper fields that carry no additional provenance.

### 8) Event construct in MUSIT and Specify flattening strategy

MUSIT is event-centric (many event rows and role/link tables per object), while Specify specimen
records are centered on `CollectionObject` + linked `CollectingEvent` + `Determination` (+ related
agents/attachments). For Oslo vascular migration, treat event data as follows:

1. **Collecting pipeline events** (`EVENT` + `COLLECTING_EVENT` + `EVENT_MUSEUM_OBJECT`) map to
   one primary `CollectingEvent` per `CollectionObject` (plus linked `Locality`/`Geography`).
2. **Identification events** (`CLASSIFICATION_EVENT`, typification links) map to
   `Determination` rows (multiple allowed), with one marked `iscurrent=true`.
3. **Role assertions** (`EVENT_ROLE_PERSON_NAME`, `EVENT_ROLE_ACTOR`) map to `Agent` links where
   Specify has a first-class target (collector, determiner, etc.); unresolved role details are kept
   in JSON payload.
4. **Event notes/documents** map to remarks/attachments/citations when possible; any unmapped
   structure is retained in JSON payload.

### 9) Historical state policy for this migration

Specify 7 has audit tables (`SpAuditLog`, `SpAuditLogField`) and an Edit History UI, but this is
application audit logging, not a native MUSIT-style event-sourcing model. In this codebase, audit
entries are explicitly written by selected backend flows (for example workbench upload and some tree
mutations), so complete historical replay from MUSIT cannot be represented 1:1 purely with core
Specify relational fields.

Migration policy here:

1. **Flatten to current-state specimen model** (normal Specify records users work with).
2. **Preserve historical/event lineage in JSON** (`CollectionObject.text1` payload above).
3. **Do not write migration audit-log records**. Load the most recent state only and keep prior-state
   detail as preserved source evidence in JSON.

### 1. Images and files

| Source | What you get | Notes for Specify `Attachment` |
|--------|----------------|----------------------------------|
| **`DIGIR_MUSIT.V_DC_*_VASCULAR.IMAGE_URI`** | Populated on many rows (e.g. ~979k non-empty for `V_DC_O_VASCULAR` in one count). | Values are **not full `https://…` URLs** in DB text; they match the **numeric id** used by the public image endpoint (same value as **`MEDIAGRUPPE_ENHETS_ID`** for that specimen’s media group). Use them to build the **Unimus URL** below. |
| **`MUSIT_BOTANIKK_FELLES.MUSEUM_OBJECT.MEDIAGRUPPE_ENHETS_ID`** | ~1.7M objects carry a media-group id. | Same id as **`IMAGE_URI`** / **`id=`** in **`web_hent_bilde.php`** (see [Public web image URL](#public-web-image-url-unimus-felles)). Join to **`USD_FELLES.MEDIA_FIL`** on **`MEDIAGRUPPE_ENHETS_ID`** for **`OPPRINNELIG_FILNAVN`**, **`ORIGINAL_KILDEHENVISNING`**, **`BILDE` / BLOB columns**, **`MEDIA_TYPE`**, **`TITTEL`**, **`FORMAT`**, etc. Multiple `MEDIA_FIL` rows per group = versions / pages. |
| **`USD_FELLES.MEDIA__PATHS`** | **`KATALOG_STI`**, **`ORIG_STI`**, schema keys (`SKJEMA_NAVN`). | **Filesystem / archive roots** on museum storage (e.g. `/usit/...`, `/usit/musitprod/...`). Specify usually needs **copied files + HTTPS** or a **separate asset pipeline**; paths are still the authoritative link between DB and file. |
| **`MUSIT_BOTANIKK_FELLES.REFERENCE_DOCUMENT`** | **`DOCUMENT_FILE`**, **`DOCUMENT_TEXT`**, **`DOCUMENT_TITLE`**, **`DOCUMENT_REFERENCE`**. | PDFs / scans linked as documents (often via **`DOCUMENT_OBJECT`** / **`EVENT_DOCUMENT`** to `OBJECT_ID` / `EVENT_ID`). |
| **`MUSIT_BOTANIKK_FELLES.V_COUNT_PHOTO`** | **`OBJECT_ID`**, **`PHOTO_COUNT`**. | Quick “has N photos” flag; resolve files via **`MEDIAGRUPPE_ENHETS_ID`** + `USD_FELLES` as above. |
| **`MUSIT_BOTANIKK_FELLES.ACTOR.URL`**, **`URL_NOTE`** | Person / org web links. | Map to **`Agent`** URL fields where appropriate (not specimen attachments). |

The [schema overview](oracle_schema_overview.md) already flags **`USD_FELLES`** as the primary **media / attachment** store for migration.

#### Public web image URL (Unimus / `felles`) {: #public-web-image-url-unimus-felles}

The **legacy MUSIT web UI** serves specimen images through a single PHP endpoint on **`www.unimus.no`**. The query parameter **`id`** is the Oracle **media group** identifier:

```text
https://www.unimus.no/felles/bilder/web_hent_bilde.php?id=<MEDIAGRUPPE_ENHETS_ID>&type=jpeg
```

| Query part | Meaning |
|------------|---------|
| **`id`** | **`USD_FELLES.MEDIAGRUPPE_ENHET.MEDIAGRUPPE_ENHETS_ID`** and **`MUSIT_BOTANIKK_FELLES.MUSEUM_OBJECT.MEDIAGRUPPE_ENHETS_ID`** (one group → one “hero” image stream in the UI). |
| **`type`** | Output format served to the browser (example: **`jpeg`** for a web-friendly derivative). Other values may exist (e.g. master **`tif`**); confirm per use case in the UI or by trial. |

**Worked example (vascular herbarium sheet, TRH)**

| Item | Value |
|------|--------|
| **`MUSEUM_OBJECT.OBJECT_ID`** | `187950` |
| **`MUSEUM_OBJECT.IDENTIFIER_STRING`** | `TRH-V-241112` |
| **`MUSEUM_OBJECT.MEDIAGRUPPE_ENHETS_ID`** | `14893715` |
| **`USD_FELLES.MEDIAGRUPPE_ENHET.MEDIAGRUPPE_UUID`** | `da66e10a-d2f5-4cc3-a8ed-593ca23a8b96` |
| **`USD_FELLES.MEDIAGRUPPE_ENHET.FILMNR_NEGATIVNR`** | `TRH-V-241112-01.tif` (default / “negative” filename on the group) |
| **`FREMVISNINGS_MEDIAFIL_ID`** | `22747455` → preferred **`MEDIA_FIL`** row for display. |
| **Public URL (verified)** | `https://www.unimus.no/felles/bilder/web_hent_bilde.php?id=14893715&type=jpeg` |

**`USD_FELLES.MEDIA_FIL`** for `MEDIAGRUPPE_ENHETS_ID = 14893715` (scalar fields only): three rows — large master **`TRH-V-241112-01.tif`** (`MEDIAFIL_ID` `22747445`, ~31 MB), and two smaller JPEGs under **`ID_I_SAMLING`** `MUSIT_BOTANIKK_FELLES_FOTO_14661224.jpg` / `…25.jpg` with different **`MEDIA_VERSJONSTYPE_ID`** (derivatives / thumbnails). The PHP endpoint abstracts which file is returned for a given **`type`**.

**Migration / Specify**

- For **`Attachment.attachmentlocation`** (or equivalent), you can store this **stable public URL** as long as Unimus keeps serving it, **or** copy the file to your own CDN and store the new URL.
- **`OBJECT_ATTRIBUTES.UUID`** is often **`NULL`** on older rows; the **media group id** (and **`MEDIAGRUPPE_UUID`**) are still sufficient to address images without DiGIR.
- **`DIGIR_MUSIT.V_DC_*_VASCULAR.IMAGE_URI`** lines up with the same **`id=`** semantics when the export row is tied to the same media group.

### 2. Identity, cataloguing, and collection grouping (Specify `CollectionObject` / `Collection`)

| Oracle | Tables / views | Specify-ish role |
|--------|------------------|--------------------|
| Catalog / DwC triple | **`DIGIR_MUSIT.V_DC_*`**: `INSTITUTIONCODE`, `COLLECTIONCODE`, `CATALOGNUMBER`, `PREVIOUSCATALOGNUMBER`, `UUID` | `CollectionObject.catalogNumber`, `uniqueidentifier` / `guid`, `AltCatalogNumber`; join UUID → **`OBJECT_ATTRIBUTES.UUID`**. |
| Internal id | **`MUSEUM_OBJECT.OBJECT_ID`** | Stable primary key for joins (not necessarily stored in Specify; use in **`oracle_to_specify_map`**). |
| Human label | **`MUSEUM_OBJECT.IDENTIFIER_STRING`**, **`LONG_NAME`**, **`IDENTIFIER_NUM`** | `CollectionObject` text fields / remarks / “name” depending on policy. |
| Workflow | **`OBJECT_ATTRIBUTES`**: `IS_REG`, `IS_CORRECTED`, `IS_APPROVED`, `REG_USER`, `KORR_USER`, `APPROVE_USER`, dates, `OBJECT_STATE`, `OBJECT_WITHHELD` | Provenance + “embargo-like” flags → Specify `Visibility` / `Embargo*` / `Remarks` / custom fields. |
| Dataset / project | **`OBJECT_ATTRIBUTES.DATASET`**, **`PROJECT_NAME`** | Collection batch / `projectnumber` / remarks (see [earlier sections](#storage-model-musit-botany)). |
| Type | **`MUSEUM_OBJECT.MUSEUM_OBJECT_TYPE`** → **`TYPES`** | Object kind (herbarium sheet vs place etc.). |
| Parent / hierarchy | **`MUSEUM_OBJECT.PARENT_OBJECT_ID`**, **`OBJECT_HIERARCHY`** | Container / duplicate / “same sheet” semantics (`SAME_SHEET_AS`, `DUBLETTES` on attributes). |

### 3. Collecting event, locality, geography (Specify `CollectingEvent` / `Locality`) {: #collecting-event-locality-geography}

| Oracle | Tables / views | Role |
|--------|----------------|------|
| When / text | **`TIMESPAN`**: `FROM_DATE`, `TO_DATE`, `TIME_AS_TEXT`, `UNCERTAIN` | Collecting date + precision. |
| Event shell | **`EVENT`**: `EVENT_ID`, `EVENTNAME`, `EVENT_TYPE`, `TIMESPAN_ID` | Link via **`EVENT_MUSEUM_OBJECT`** to `OBJECT_ID`. |
| Field event | **`COLLECTING_EVENT`**: `COLLECTIONTYPE_ID`, `LEGNAME_ORIG`, `AGG_PERSONNAMES` | Collector aggregation + “plant collecting” type (see [migration_strategy](migration_strategy.md) for intended `COLLECTIONTYPE_ID` meaning). |
| Place | **`PLACE`**: `PLACE_ID`, `PLACE_NAME_AGG` | Locality string; drill via **`PLACE_*`** junction tables to **`ADMINISTRATIVE_PLACE`**, **`INDEXED_LOCALITY`**, **`STORING_PLACE`**, **`ECOLOGY_PLACE`**, etc. |
| Coordinates | **`KOORDINATE_PLACE`**: lat/long, UTM/MGRS, datum, precision, sources, verbatim strings | `Locality` / `CollectingEvent` geo fields; DiGIR view already flattens many DwC geo columns for vascular exports. |
| DwC extras on export | **`V_DC_*`**: locality, country, county, elevation, depth, `ORIGINAL_KOORDINAT_STRENG`, `KOORDINATKILDE`, `BIOGEOREGION`, `DATASETNAME`, … | Good for parity with IPT; cross-check against normalized **`PLACE`** / **`KOORDINATE_PLACE`** when you need authoritative MUSIT values. |

### 4. Taxonomy and determinations (Specify `Determination` / `Taxon`)

| Oracle | Tables | Role |
|--------|--------|------|
| Current ID event | **`CLASSIFICATION_EVENT`** → **`CLASSIFICATION_TERM`**, **`CLASSIFICATION_TAXON`** → **`TAXON`** → **`LATIN_NAMES`** | Determination + scientific name; **`ADB_TAXON_ID`** links Artsdatabanken (NorTaxa strategy in [migration_strategy](migration_strategy.md)). |
| Type status | **`TYPIFICATION_EVENT`**, **`TYPE_SPECIMEN`** | Type specimen / typification. |
| DwC-style ranks on export | **`V_DC_*`**: kingdom … species, `SCIENTIFICNAMEAUTHOR`, `IDENTIFICATION*` columns, Norwegisk taxon ids (`NRIKEID` … `NARTID`) | IPT parity + bridge to NorTaxa. |

### 5. People and agents (Specify `Agent`)

| Oracle | Tables | Role |
|--------|--------|------|
| Actors | **`ACTOR`**, **`PERSON_NAME`**, **`PERSON_INFORMATION`**, **`GROUPMEMBERSHIP`** | Collectors, determiners, organisations (`migrate_musit_agents` scope). |
| Event roles | **`EVENT_ROLE_ACTOR`**, **`EVENT_ROLE_PERSON_NAME`** | Who did collecting, ID, loans, etc. |
| DwC | **`V_DC_*`**: `COLLECTOR`, `IDENTIFIEDBY`, `RECORDEDBYID`, `IDENTIFIEDBYID` | String + ORCID/Wikidata-style ids (your SQL already normalises separators). |

### 6. Identifiers, notes, literature, legacy (misc. Specify fields)

| Oracle | Tables | Role |
|--------|--------|------|
| Barcodes / other ids | **`IDENTIFIER_ASSIGNMENT`** (`EVENT_ID` or `OBJECT_ID`, `IDENTIFIER_STRING`, `IDENTIFIER_TYPE`) | `CollectionObject` alt numbers / preparations / identifier table patterns. |
| Free text | **`NOTE`**, **`MUSEUM_OBJECT_NOTE`**, **`EVENT_NOTE`**, **`OBJECT_ATTRIBUTES.ANALYSIS_REQUEST`** | `Remarks`, attachment notes, workbench text fields. |
| Literature | **`REFERENCE_DOCUMENT`** + event links | `ReferenceWork` / citations depending on model. |
| Full legacy blob | **`LEGACY_EVENT.LEGACY_DATA`** (JSON) | Extra DwC-style keys not normalised into MUSIT tables (good for gap-fill and auditing). |

### 7. Specify-side targets (reminder)

Core Specify tables touched by a typical herbarium migration include **`collectionobject`** (catalog numbers, GUID, field number, remarks, `CollectingEventID`, `CollectionID`, … — see `Collectionobject` in `specify7/specifyweb/specify/models.py`), **`collectingevent`**, **`locality`**, **`determination`**, **`taxon`**, **`agent`**, and **`attachment`** / **`collectionobjectattachment`** (`attachmentlocation`, `origfilename`, `title`, `ispublic`, …). Map Oracle columns above into those after you fix **one** canonical rule for **files** (path + filename + MIME from `MEDIA_FIL` + `MEDIA__PATHS`).

## Related tooling

- **`scripts/oracle_sql.py`** / shell function **`oracle_sql`** after `source scripts/port-forward.sh` — see module docstring for thick client (Instant Client) on macOS.
- **`scripts/port-forward.sh`** — Oracle tunnel via cluster pod; see comments in the script.
