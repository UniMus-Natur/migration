# Oracle Source Schema Overview

> Auto-generated analysis of `schemas/schema.dbml` (10,182 lines, ~260 KB).  
> Generated: 2026-03-12

---

## Executive Summary

The source Oracle database contains **~890 tables** spread across **30 distinct schemas (Oracle users/owners)**. The vast majority of these tables are either:

1. **Oracle system infrastructure** — can be completely ignored for migration purposes.
2. **Legacy per-museum "USD" data schemas** — the *actual specimen data* we need to move.
3. **MUSIT application schemas** — a refactored, event-sourced layer that was built on top of the USD legacy data. These are the schemas that need to be mapped to Specify 7.

---

## Schema Inventory

| Schema | Tables | Category | Migrate? |
|---|---|---|---|
| `MUSIT_BOTANIKK_FELLES` | 111 | MUSIT core – Botany | ✅ Primary |
| `MUSIT_ZOOLOGI_ENTOMOLOGI` | 101 | MUSIT core – Entomology | ✅ Primary |
| `USD_BOTANIKK_TRONDHEIM` | 81 | Legacy botany data – Trondheim | ✅ Source data |
| `USD_BOTANIKK_TROMSO` | 78 | Legacy botany data – Tromsø | ✅ Source data |
| `USD_BOTANIKK_SVALBARD` | 73 | Legacy botany data – Svalbard | ✅ Source data |
| `USD_BOTANIKK_BERGEN` | 66 | Legacy botany data – Bergen | ✅ Source data |
| `USD_FELLES` | 45 | Shared media / image storage | ⚠️ Media only |
| `USD_METADATA` | 43 | USD app config; `BRUKARAR`/`GRUPPE` needed for users | ⚠️ Partial |
| `USD_NAT_TAXAREG` | 22 | Shared taxonomic registry (fallback for unmatched taxa) | ⚠️ Reference |
| `USD_TRANSLATION` | 6 | UI string translations | ❌ Ignore |
| `USD_MUSEUM` | 2 | Museum metadata | ❌ Ignore |
| `USD` | 2 | Misc shared | ❌ Ignore |
| `USD_WEB` | 1 | Web app config | ❌ Ignore |
| `USD_FELLES_MIGRERING` | 1 | Media migration scratch table | ❌ Ignore |
| `USD_BOT_BERGEN_BACK` | 1 | Old Bergen backup | ❌ Ignore |
| `USD_ETNO_OS` | 1 | Ethnography Oslo (unrelated) | ❌ Ignore |
| `SDE` | 70 | ArcGIS/Esri spatial data engine | ❌ Ignore |
| `MDSYS` | 53 | Oracle Spatial/Locator | ❌ Ignore |
| `SYS` | 43 | Oracle core system | ❌ Ignore |
| `SYSTEM` | 7 | Oracle system | ❌ Ignore |
| `XDB` | 6 | Oracle XML database | ❌ Ignore |
| `CTXSYS` | 5 | Oracle Text / full-text search | ❌ Ignore |
| `CSMIG` | 12 | Oracle character-set migration | ❌ Ignore |
| `MUSIT_BOTANIKK_FELLES_FOTO` | 11 | Botany photo archive | ⚠️ Media only |
| `MUSIT_ZOOLOGI_ENTOMOLOGI_FOTO` | 11 | Entomology photo archive | ⚠️ Media only |
| `MUSIT_NATHIST_FELLES` | 3 | Shared natural history helpers | ⚠️ Reference |
| `MUSIT_COORDINATE` | 2 | Coordinate conversion testing | ❌ Ignore |
| `MUSIT_ROLE_ADMIN` | 3 | MUSIT role/ACL admin | ❌ Ignore |
| `DIGIR_MUSIT` | 3 | DiGIR/DwC public export view | ❌ Ignore |

---

## The Two Main Schema Families

### Family 1 — USD Schemas (Legacy, Per-Museum)

These are the **oldest and most data-rich** schemas. Each Norwegian natural history museum had its own application called **USD** (Universitetsmuseenes Samlingsdata) — in effect a custom CMS for collection management.

Each per-museum botany schema (`USD_BOTANIKK_TRONDHEIM`, `USD_BOTANIKK_TROMSO`, `USD_BOTANIKK_BERGEN`, `USD_BOTANIKK_SVALBARD`) follows the same pattern and contains the actual herbarium specimen records.

**Key tables in USD botany schemas:**

| Table | Norwegian name explained | Purpose |
|---|---|---|
| `FUNNETIKETT` | "Find label" | **Core specimen record** — the herbarium sheet label, with locality, date, collector, ecology, etc. |
| `EKSEMPLAR` | "Specimen/copy" | Physical specimen instance, linked to a `FUNNETIKETT` |
| `BESTEMMELSE` | "Determination" | Taxonomic identification event(s) for an eksemplar |
| `GEOREG` | "Geo register" | Geographic lookup table (municipality/county grid areas) |
| `ADMINISTRATIVTSTED` | "Administrative place" | Hierarchical administrative place names (land/fylke/kommune) |
| `KOORDINATSETT` | "Coordinate set" | UTM/geo coordinates for a collecting location |
| `LEGSAMLER` | "Leg./collector" | Collector persons linked to a funnetikett |
| `DETBESTEMMER` | "Det./determiner" | Person(s) who performed a determination |
| `AUTORPERSON` | "Author person" | Taxonomic authors |
| `HERBTAXAREG` / `TAXAREG` | "Herb taxonomy register" | Per-herbarium taxonomic name register |
| `NORSKNAVN` | "Norwegian name" | Common Norwegian names for taxa |
| `HERBARIE` | "Herbarium" | The herbarium/collection the specimen belongs to |
| `INNSAMLINGSMETODE` | "Collecting method" | Method of collection |
| `KONSERVERINGSMETODE` | "Conservation method" | Preservation method |
| `KOORDINAT` | "Coordinate" | Spatial coordinates (alternative, simpler table) |
| `BRUKERE` | "Users" | Application users — must migrate as Specify **SpecifyUser** accounts |
| `PERSONER` | "Persons" | Person contact details linked to `LEGSAMLER`/`DETBESTEMMER` — migrate as **Agents** |

**Key USD naming patterns:**
- Norwegian words throughout — most table/column names are Norwegian
- `_ID` suffix = primary/foreign key integer (e.g. `ETIKETT_ID`, `EKSEMPLAR_ID`)
- `NR` suffix = number/sequential identifier (e.g. `REGNR` = registration number)
- `DATO` = date (e.g. `REG_DATO` = registration date)
- `KOMMENTAR` = comment/note
- `ER_*` prefix = boolean "is" field (e.g. `ER_GODKJENT` = "is approved")
- `INNSKREVET_*` = "as entered/typed" (free text version of a structured field)
- `CA_*` = "circa" / uncertain prefix
- `USIKKER_*` = uncertain

**Tables to ignore in USD schemas:**
- `BRUKER_HISTORIE` — user session history log (not needed in Specify)
- `HURTIGTASTER`, `HURTIGTAST_OPPSETT` — keyboard shortcut config
- `DEBUGTABLE`, `DEBUG`, `DEBUGTABLE3` — debugging artifacts
- `GAMMEL_*`, `GAMMELTT*` — "old/legacy" migration backups
- `*_BACK` suffix tables — backup snapshots
- `*VM_IMPORT*` tables — one-time import staging tables
- `PLAN_TABLE` — Oracle EXPLAIN PLAN cache

> ⚠️ **`BRUKERE` and `PERSONER` are NOT safe to ignore** — see the [Persons, Agents & Users](#persons-agents--users) section below.

---

### Family 2 — MUSIT Schemas (Event-Sourced, Structured)

`MUSIT_BOTANIKK_FELLES` and `MUSIT_ZOOLOGI_ENTOMOLOGI` represent a **later, more structured layer** built on top of the USD data. The architecture uses an **event-sourcing pattern** — almost all state changes are represented through events.

This is where Specify 7 data should be mapped **from** during migration.

**Core architectural pattern:**

```
MUSEUM_OBJECT → (via EVENT_MUSEUM_OBJECT) → EVENT
                                              ├── COLLECTING_EVENT
                                              ├── CLASSIFICATION_EVENT
                                              ├── TYPIFICATION_EVENT
                                              ├── CONSERVATION_EVENT
                                              ├── LENDING_EVENT
                                              ├── MOVING_EVENT
                                              ├── OBSERVATION_EVENT
                                              ├── DNA_SAMPLING_EVENT
                                              ├── MEASURMENT_EVENT
                                              ├── DATABASE_EVENT
                                              └── IDENTIFIER_ASSIGNMENT (event)

EVENT → ACTOR / PERSON_NAME (via EVENT_ROLE_ACTOR / EVENT_ROLE_PERSON_NAME)
EVENT → PLACE (via PLACE_EVENT_ROLE)
PLACE → KOORDINATE_PLACE (coordinates)
PLACE → ADMINISTRATIVE_PLACE (admin hierarchy)
PLACE → INDEXED_LOCALITY (named locality)
```

**Key MUSIT tables:**

| Table | Purpose |
|---|---|
| `MUSEUM_OBJECT` | Central specimen/object entity |
| `OBJECT_ATTRIBUTES` | Metadata, UUID, workflow status; optional **`DATASET`** and **`PROJECT_NAME`** strings (see [Oracle botany datasets](oracle_botany_datasets.md)) |
| `EVENT` | Abstract base for all events, with a `TIMESPAN_ID` |
| `TIMESPAN` | Date ranges (from/to dates, text representation, uncertainty flag) |
| `CLASSIFICATION_EVENT` | Determination/ID event |
| `CLASSIFICATION_TERM` | The taxon name as determined |
| `COLLECTING_EVENT` | Collecting/field event |
| `ACTOR` | A person or organization |
| `PERSON_NAME` | Multiple names for an actor (surname, given, title) |
| `PLACE` | Abstract place aggregate |
| `KOORDINATE_PLACE` | Detailed coordinates (UTM, MGRS, lat/long, depth, altitude) |
| `ADMINISTRATIVE_PLACE` | Administrative hierarchy (country > region > municipality) |
| `INDEXED_LOCALITY` | Named collecting localities |
| `LATIN_NAMES` | Taxonomic name register, with validity and parent chain |
| `TAXON` | Canonical taxon linking valid Latin names |
| `TAXON_CATHEGORY` | Taxonomic rank (species, genus, family, etc.) |
| `AUTHORSTRINGS` | Taxonomic author strings |
| `ROLES` | Vocabulary for roles (collector, identifier, etc.) |
| `TYPES` | Vocabulary for type metadata |
| `STORING_PLACE` | Physical storage location (mag/section/shelf) |
| `REFERENCE_DOCUMENT` | Literature references |
| `NOTE` | Free-text notes |
| `TYPE_SPECIMEN` | Type specimen designations |
| `TYPIFICATION_EVENT` | Type designation events |
| `GENDERS_AND_STAGES` | Sex + life stage records |
| `USER_COLLECTION_SEQS` | Sequence number management per collection |
| `IDENTIFIER_ASSIGNMENT` | Museum number / barcode assignment |
| `LEGACY_EVENT` | Stores raw legacy data blob for events imported from USD |

**Tables to ignore in MUSIT schemas:**
- `TMP_*` — temporary staging tables
- `TMP_COPY_TAXON`, `TMP_LATNAME_OLD_NEW`, `TMP_TAXON_*` — one-off taxon migration helpers
- `TMP_ROLES`, `TMP_TYPES`, `TMP_VIEW_ROLES` — temp config tables
- `TMP_USER_ROLES` — contains Oracle usernames but **check** if useful for Specify user creation
- `XML_TAXON`, `XML_TAXON_RESULT`, `XML_TAXON_RESULT_ALL` — deprecated XML query cache
- `T_ROLES`, `T_TYPES` — likely temp test tables (no `_ID` suffix = no PK structure)
- `ERROR_LOG` — Oracle procedure error logging
- `ODBC_IMPORT_DELETE` — ODBC import artifact
- `PERSON_INFORMATION` — travel logs, unrelated to specimens
- `GEOREG_VM_IMPORT`, `HOVEDREG_VM_IMPORT`, `TAXAREG_VM_IMPORT`, etc. — import staging
- `ZZ_*` prefix tables — deprecated/retired tables (double-Z prefix convention)
- `TABLE_NAME`, `TABLE_NAMES` — internal metadata tables  
- `LEGACY_EVENT` — may be useful as a fallback reference but not for direct mapping

> ⚠️ **`ACTOR` and `PERSON_NAME` are NOT safe to ignore** — see the [Persons, Agents & Users](#persons-agents--users) section below.

---

## Persons, Agents & Users

There are **three overlapping categories** of "people" in the source schema, each mapping to a different concept in Specify 7. They need to be migrated and then cross-linked.

> **Implemented flows:** application users from `USD_METADATA.BRUKARAR` → `flows/migrate_users.py` (see [**User migration report**](user_migration_report.md)). MUSIT `ACTOR` + `PERSON_NAME` (botany + entomology schemas) → `flows/migrate_musit_agents.py` (see [**MUSIT collection agents migration**](migrate_musit_agents.md)).

### Category 1 — Biological Agents (Collectors, Determiners, Authors)

**Target in Specify 7:** `Agent` (SpecifyAgent table)

These are real-world people (or organisations) associated with specimens — collectors, determiners, type authors. They appear in multiple places:

| Source schema | Tables | Role |
|---|---|---|
| `MUSIT_BOTANIKK_FELLES` | `ACTOR`, `PERSON_NAME`, `GROUPMEMBERSHIP` | Primary structured agent store |
| `MUSIT_ZOOLOGI_ENTOMOLOGI` | `ACTOR`, `PERSON_NAME`, `GROUPMEMBERSHIP` | Same, for entomology |
| `USD_BOTANIKK_*` | `PERSONER` (person details), `LEGSAMLER` (collector link), `DETBESTEMMER` (determiner link) | Per-museum legacy agent records |
| `USD_NAT_TAXAREG` / `USD_BOTANIKK_*` | `AUTORPERSON`, `AUTOR_LISTE` | Taxonomic name authors |

**MUSIT `ACTOR` model:**
- `ACTOR` — the entity (person or group), with `ACTOR_TYPE`, birth/death dates, email, institution
- `PERSON_NAME` — multiple name records per actor (type: preferred, variant, etc.) with `PERSON_SURNAME`, `PERSON_GIVEN_NAME`, `TITLE`
- `GROUPMEMBERSHIP` — actors can be groups containing member actors
- `AUTHORSTRINGS` — pre-formatted author citation strings

The MUSIT `ACTOR` table is the **canonical source** for agents. The USD `PERSONER` tables and legacy `AUTORPERSON` tables are likely partially overlapping — de-duplication will be needed.

### Category 2 — Application Users (Museum Staff with Login Accounts)

**Target in Specify 7:** `SpecifyUser` + `Agent` (users are always linked to an agent)

These are the staff who actually log into the system. They need Specify 7 login accounts, plus matching Agent records so their edits are attributable.

| Source schema | Table | Contents |
|---|---|---|
| `USD_BOTANIKK_*` | `BRUKERE` | Per-museum application users: Oracle `USER_ID`/`USER_NAME`, number range allocations, herbarium affiliation (`HERB_ID`) |
| `USD_METADATA` | `BRUKARAR` | Centralised MUSIT user store: username, name, email, phone, institution, FEIDE (national SSO) identity |
| `USD_METADATA` | `BRUKERNAVN_GRUPPE` | User → group memberships |
| `USD_METADATA` | `GRUPPE` | User groups with museum affiliation |
| `MUSIT_ROLE_ADMIN` | `ROLES_FOR_USER`, `ROLE_USER` | MUSIT-level role assignments |

`USD_METADATA.BRUKARAR` is the best source — it has full contact details including FEIDE (Norwegian national identity) which could help match to real people and potentially to existing Specify installations.

**Mapping approach:**
1. Each user in `BRUKARAR` → one `SpecifyUser` in Specify 7
2. Each user should also resolve to (or create) an `Agent` — match on name/email to `ACTOR`
3. Role/group memberships → Specify 7 user groups / collection access

### Category 3 — Oracle System Accounts

**Target in Specify 7:** none (discard)

Oracle-internal accounts referenced by column `ORA_USERNAME` in `DATABASE_EVENT` — these are just DB audit trails, not real user accounts to migrate.

### Key Challenge: De-duplication Across Sources

The same person likely appears in **all three sources** independently:
- As a `ACTOR` in MUSIT (with structured name parts)
- As a row in USD `PERSONER` (with possibly a different spelling)
- As an `AUTORPERSON` entry (abbreviated for taxonomy)
- As a `BRUKARAR` entry (with their email)

The `ACTOR.VALID_PERSON_NAME_ID` field already points to the canonical name within MUSIT, which is a good starting point. Merging across schemas will likely require string-similarity matching + manual review for ambiguous cases.

---

## Naming Conventions Summary

### Prefix conventions (tables)
| Prefix | Meaning |
|---|---|
| `TMP_` | Temporary / one-off import staging — **ignore** |
| `ZZ_` / `ZZPLACE_` | Deprecated / retired tables — **ignore** |
| `IMP_` | Import staging tables — **ignore** |
| `T_` | Suspected temp test copies — **ignore** |
| `XML_` | Legacy XML query caches — **ignore** |
| `GAMMEL_` | Norwegian "old" — old/backup data — **ignore** |
| `*_BACK` | Explicit backup snapshots — **ignore** |
| `DEBUG*` | Debug tables — **ignore** |
| `TEST_*` | Test tables — **ignore** |
| `*_VM_IMPORT` | VisualMuseum import staging — **ignore** |

### Column naming conventions
| Pattern | Meaning |
|---|---|
| `*_ID` | Surrogate integer primary/foreign key |
| `*NR` / `*_NR` | Sequential number (reg. number, sub-number) |
| `ER_*` | Boolean "is/are" (Y/N values) |
| `INNSKREVET_*` | "As entered" — free-text original input |
| `TOLKET_*` | "Interpreted" — parsed/normalised version |
| `USIKKER_*` | "Uncertain" qualifier |
| `CA_*` | "Circa" — approximate value |
| `AGG_*` | Aggregated / denormalized text |
| `KTRL#*` | Oracle Forms control fields — access control, **ignore** |
| `DATO` / `*_DATO` | Date field |
| `KOMMENTAR` / `MERKNAD` | Free text comment/remark |
| `ORA_*` | Oracle-specific internal (e.g. `ORA_USERNAME`) |
| `ADB_*` | References to external **ADB** (Artsdatabanken) IDs |
| `NHM_*` | References to NHM (Natural History Museum) external IDs |
| `UUID` | UUID field — important for cross-system identity |

---

## Schemas to Completely Ignore

The following schema groups contain **zero application data** relevant to specimen migration:

### Oracle System Schemas
- `SYS` — Oracle internal data dictionary
- `SYSTEM` — Oracle system tables
- `MDSYS` — Oracle Spatial/SDO (coordinate system definitions etc.)
- `CTXSYS` — Oracle Text (full-text index metadata)
- `XDB` — Oracle XML Database
- `CSMIG` — Oracle character-set migration tooling

### Infrastructure Schemas (3rd party)
- `SDE` — ArcGIS/Esri SDE spatial data engine schema (GDB_* tables, geometry networks, etc.)

### Application Config / Metadata Only
- `USD_METADATA` — USD application configuration (forms, search setups, field definitions, report templates). **Exception:** `BRUKARAR`, `BRUKERNAVN_GRUPPE`, and `GRUPPE` must be migrated for user accounts — see [Persons, Agents & Users](#persons-agents--users).
- `USD_TRANSLATION` — UI string translations
- `USD_MUSEUM` — Museum top-level config
- `USD_WEB` — Web app configuration
- `MUSIT_ROLE_ADMIN` — Role-based access control registry (individual roles will be recreated by name in Specify, not imported as rows)
- `MUSIT_COORDINATE` — Coordinate parser test log
- `MUSIT_NATHIST_FELLES` — Minimal (3 tables: biogeographic regions, coordinate test log). Low signal.

### Legacy/Dead Data
- `DIGIR_MUSIT` — DiGIR/Darwin Core export view remnant (`DARWIN_CORE_NORSK` is a denormalised view snapshot, plus SDE log tables)
- `MUSIT_BOTANIKK_FELLES_FOTO` — Legacy photo archive for botany (Norwegian named: FOTOARK, GJENSTAND, STED, SAMLING). The USD_FELLES schema supersedes this.
- `MUSIT_ZOOLOGI_ENTOMOLOGI_FOTO` — Same as above for entomology
- `USD_FELLES_MIGRERING` — Single table: `MEDIA_FIL_MED_FULL_STI` (scratch migration helper)
- `USD_BOT_BERGEN_BACK` — Single old backup table for Bergen
- `USD_ETNO_OS` — Ethnography Oslo (only `BRUKER` table, completely unrelated domain)

---

## Migration Priority Map

```
HIGH PRIORITY (specimen + taxonomy data)
├── USD_BOTANIKK_TRONDHEIM   → MUSIT_BOTANIKK_FELLES  → Specify7 (Botany, TRH)
├── USD_BOTANIKK_TROMSO      → MUSIT_BOTANIKK_FELLES  → Specify7 (Botany, TMS)
├── USD_BOTANIKK_SVALBARD    → MUSIT_BOTANIKK_FELLES  → Specify7 (Botany, SVA)
├── USD_BOTANIKK_BERGEN      → MUSIT_BOTANIKK_FELLES  → Specify7 (Botany, BRG)
└── MUSIT_ZOOLOGI_ENTOMOLOGI                           → Specify7 (Entomology)

HIGH PRIORITY (persons & users — needed to link records)
├── MUSIT_BOTANIKK_FELLES.ACTOR + PERSON_NAME          → Specify7 Agent
├── MUSIT_ZOOLOGI_ENTOMOLOGI.ACTOR + PERSON_NAME       → Specify7 Agent
├── USD_BOTANIKK_*.PERSONER + AUTORPERSON              → Specify7 Agent (merge/dedup)
└── USD_METADATA.BRUKARAR + GRUPPE                     → Specify7 SpecifyUser + Agent

MEDIUM PRIORITY (taxonomy fallback — unmatched taxa only)
└── USD_NAT_TAXAREG          → Specify7 (for taxa absent from NorTaxa; NorTaxa is primary source)

MEDIA / ATTACHMENTS (separate concern)
└── USD_FELLES               → Specify7 (Attachments)

IGNORE
├── All Oracle system schemas (SYS, SYSTEM, MDSYS, etc.)
├── SDE
├── USD_METADATA (config tables only — not BRUKARAR/GRUPPE)
├── USD_TRANSLATION, USD_WEB, USD_MUSEUM
├── MUSIT_ROLE_ADMIN (roles referenced by name in Specify, not IDs)
├── MUSIT_COORDINATE
├── DIGIR_MUSIT
└── *_FOTO schemas
```

---

## Key Observations for Migration

1. **Duplicate schemas per museum**: `USD_BOTANIKK_TRONDHEIM`, `USD_BOTANIKK_TROMSO`, `USD_BOTANIKK_SVALBARD`, `USD_BOTANIKK_BERGEN` are nearly structurally identical. The same migration logic should apply to all four with a schema parameter.

2. **MUSIT is partially migrated USD**: `MUSIT_BOTANIKK_FELLES` contains a `LEGACY_EVENT` table (with a `LEGACY_DATA CLOB`) — this is where raw USD data was preserved during the MUSIT migration. This is a useful fallback if MUSIT data is incomplete.

3. **Event-based IDs are shared**: In the MUSIT schemas, `EVENT_ID` is both the primary key of `EVENT` and the primary key of all type-specific event tables (e.g. `COLLECTING_EVENT.EVENT_ID`, `CLASSIFICATION_EVENT.EVENT_ID`). These are table-per-type inheritance, not separate entities.

4. **Norwegian mixed with English**: MUSIT schemas use English naming (`EVENT`, `MUSEUM_OBJECT`, `ACTOR`), while USD schemas use Norwegian (`EKSEMPLAR`, `FUNNETIKETT`, `BESTEMMELSE`). Column names, however, are Norwegian inside both.

5. **`ZZ_` and `TMP_` are dead tables**: The `ZZ_` prefix is the application convention for "deprecated but not yet dropped." `TMP_` tables were used for one-time datamigration operations. Both can be excluded entirely.

6. **UUID fields exist**: `OBJECT_ATTRIBUTES.UUID` and `FUNNETIKETT.TEKSTLIG_ID` (unique) provide cross-system stable identifiers. These should be preserved during migration.

7. **Coordinate complexity**: The MUSIT schema has a very rich coordinate model (`KOORDINATE_PLACE`) supporting UTM, UTM33, MGRS, lat/long, depth, altitude — all as range pairs (L/H = low/high). The `DERIVED_COORDINATES` table contains pre-computed conversions.

8. **Taxon model is duplicated across MUSIT schemas**: Both `MUSIT_BOTANIKK_FELLES` and `MUSIT_ZOOLOGI_ENTOMOLOGI` maintain their own copies of `LATIN_NAMES`, `TAXON`, `TAXON_CATHEGORY`, `AUTHORSTRINGS`, etc. However, the migration strategy uses **NorTaxa (Artsdatabanken) as the canonical taxonomy tree**, matched via `ADB_TAXON_ID`. Oracle taxa not found in NorTaxa are inserted as supplementary nodes — so cross-schema merge/dedup is not the primary concern; NorTaxa alignment is.

9. **`USD_NAT_TAXAREG` is the canonical taxon authority**: The shared `USD_NAT_TAXAREG` schema contains the master taxonomic register (`GAMMELTTAXAREG`, `AUTORPERSON`, `NORSKNAVN`, `NHM_LAV_IMPORT`) that drives the per-museum schemas. Important for taxonomy reconciliation.

10. **Users and Agents must be migrated before specimens**: Specify 7 requires `Agent` records to exist before `CollectionObject` records can be linked to collectors/determiners. Migrate `ACTOR`/`PERSON_NAME` and `BRUKARAR` first, establish Specify user accounts, then migrate specimens with correct foreign keys.

11. **`BRUKARAR.FEIDE` enables identity bridging**: The `FEIDE` field in `USD_METADATA.BRUKARAR` is the Norwegian national SSO identity (Feide/UNINETT). If your Specify instance will use institutional SSO, this is the key to match accounts. Even without SSO, the email (`FEIDE_EPOST`) is useful for matching humans to their existing institutional accounts.
