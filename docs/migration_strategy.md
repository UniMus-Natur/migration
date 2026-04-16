# Migration Strategy: Phased Approach to a Single Specify 7 Database

---

## The Core Problem

We are migrating **multiple collections** from a source Oracle database with **partially duplicated shared data** (taxonomy, geography, persons) into a **single Specify 7 database**. Each collection needs to be testable independently before the next one goes in.

The challenge is that shared data — agents, taxonomy, geography — is global in Specify 7 and referenced by *all* collections. It cannot be migrated incrementally. Specimen data *can*.

---

## Specify 7 Database Structure (Brief Recap)

```
Institution
└── Discipline  (e.g. "Botany", "Zoology")
    ├── TaxonTree          ← SHARED within discipline
    ├── GeographyTree      ← SHARED across institution
    ├── AgentTable         ← SHARED across institution
    └── Collection         ← ONE PER DATASET (karplanter, mosses, marine…)
        └── CollectionObject  ← migrated per dataset
```

**Key rule:** `Agent`, `Geography`, and `Taxon` records are shared across all collections. A `CollectionObject` in "Karplanter" and one in "Mosses" can point to the *same* `Agent` (collector) and the *same* `Taxon` node. This is exactly what we want — but it means these shared tables must be fully in place before any specimen migration begins.

### Infrastructure-as-code: hierarchy YAML

After the database has been bootstrapped once (institution and first guided setup), additional divisions, disciplines, and collections can be kept in version control and applied idempotently:

- **Config:** [`config/specify_structure/unimus_natur.yaml`](../config/specify_structure/unimus_natur.yaml) (edit or add sibling files per environment).
- **Flow:** [`flows/sync_specify_structure.py`](../flows/sync_specify_structure.py) — Prefect entrypoint `sync_specify_structure_flow`. Uses the same `DB_*` environment variables as other Specify flows (via [`flows/lib/specify_setup.py`](../flows/lib/specify_setup.py)). Default is `dry_run: true`; set `dry_run: false` only when applying to a target database intentionally.
- **Deployment:** `sync-specify-structure-dev` in [`prefect.yaml`](../prefect.yaml).

Reports are written under the `specify-structure-sync` category in the migration-reports S3 prefix when `S3_BUCKET` is set (same pattern as other migration flows).

---

## Dataset Groups (Collections in Specify 7)

In the Oracle source, specimens group into datasets via:

| Oracle field | Where | What it means |
|---|---|---|
| `FUNNETIKETT.HERB_ID` → `HERBARIE.HERB_FORK` | USD schemas | The herbarium sub-collection abbreviation (e.g. "V", "M", "L") |
| `COLLECTING_EVENT.COLLECTIONTYPE_ID` | MUSIT schemas | Organism group / collection type |
| `MUSEUM_OBJECT.SUB_COLLECTION_ID` + `USER_COLLECTION_SEQS.SUBCOLLECTION` | MUSIT schemas | Sub-collection numbering series |

Expected dataset → Specify Collection mapping (verify against actual `HERBARIE` contents):

| Source dataset (HERB_FORK / organism group) | Likely Specify Collection name | Discipline |
|---|---|---|
| Karplanter (vascular plants) | Karplanter | Botany |
| Mosser (bryophytes) | Mosser | Botany |
| Lav (lichens) | Lav | Botany |
| Alger (algae) | Alger | Botany |
| Sopp (fungi) | Sopp | Botany |
| Kryptogamer (cryptogams, Svalbard) | Kryptogamer | Botany |
| Marine inv. / fish | Marin | Zoology/Entomology |
| Entomologi | Entomologi | Zoology/Entomology |

> ⚠️ Confirm exact groupings by running `SELECT HERB_ID, HERB_FORK, NAVN FROM HERBARIE` in each USD schema before mapping.

---

## Two-Phase Migration

### Phase 1 — Shared Foundation (migrate ONCE, in dependency order)

This phase populates Specify 7 with all the shared reference data. **No specimens yet.** This is a one-time operation that must complete and be validated before Phase 2 starts.

```
Step 1.1 — Agents
Step 1.2 — Geography  
Step 1.3 — Taxonomy (one tree per discipline)
Step 1.4 — Application Users + SpecifyUser accounts
```

Each step is both a migration and a **validation checkpoint** — see "Validation Gates" below.

#### Step 1.1 — Agents

**Source:** `MUSIT_BOTANIKK_FELLES.ACTOR` + `PERSON_NAME` + `GROUPMEMBERSHIP` + `AUTHORSTRINGS`  
**Also:** `USD_BOTANIKK_*.PERSONER`, `USD_BOTANIKK_*.AUTORPERSON`, `USD_NAT_TAXAREG.AUTORPERSON`  
**Target:** Specify `Agent` table

An implemented subset — MUSIT **`ACTOR`** + **`PERSON_NAME`** for **`MUSIT_BOTANIKK_FELLES`** and **`MUSIT_ZOOLOGI_ENTOMOLOGI`** — is loaded by the Prefect flow **`migrate_musit_agents_flow`** (`flows/migrate_musit_agents.py`). Scope, idempotency, and gaps (USD persons, authors, deduplication) are documented in [**MUSIT collection agents migration**](migrate_musit_agents.md).

**Merge strategy:**
1. Start with MUSIT `ACTOR` as canonical — it has the most structured data (birth/death, ORCID, institution).
2. Match USD `PERSONER` against `ACTOR` by name string similarity → link or create new Agent.
3. Match `AUTORPERSON` / `AUTOR_LISTE` against Agents — these are often abbreviations (e.g. "L.", "Sw.") so match rules are different.
4. Preserve original source IDs in Specify `Agent.Remarks` or a custom field for traceability.

**Key fields to map:**

| Oracle | Specify |
|---|---|
| `ACTOR.ACTOR_TYPE` (0=person, 1=org, 2=group) | `Agent.AgentType` |
| `PERSON_NAME.PERSON_SURNAME` | `Agent.LastName` |
| `PERSON_NAME.PERSON_GIVEN_NAME` | `Agent.FirstName` |
| `ACTOR.BIRTHDATE` / `DEATHDATE` | `Agent.DateOfBirth` / `DateOfDeath` |
| `ACTOR.INSTITUTION` | linked `Agent` (org) |
| `GROUPMEMBERSHIP` | `Agent` group members |
| `AUTHORSTRINGS.AUTHORSTRING` | `Agent.Abbreviation` |

#### Step 1.2 — Geography

**Source:** `MUSIT_BOTANIKK_FELLES.ADMINISTRATIVE_PLACE` + `USD_BOTANIKK_*.ADMINISTRATIVTSTED` + `USD_BOTANIKK_*.GEOREG`  
**Target:** Specify `Geography` tree (custom — built from MUSIT, not from an external standard)

**Approach: MUSIT-first, preserve historical names**

We build our own geography tree from the Oracle data rather than using a standard hierarchy (GeoNames etc.). The reason is historical fidelity: specimens collected in 1887 in "Christiania", or in a municipality that was merged or split in the 2020 kommunereform, must remain permanently linked to the name that was correct *when the collecting happened*. Future digitisation of unregistered old records will also need these historical names.

MUSIT already solved this — `ADMINISTRATIVE_PLACE` was built to hold historical names alongside current ones, not replace them.

**The Norwegian administrative change problem:**

| Era | Issue | Impact on geography tree |
|---|---|---|
| Pre-1960 | Old names: "Christiania" (→ Oslo), pre-reform county names (Akershus etc.) | Must be in tree as own nodes |
| 2020 kommunereform | ~430 municipalities merged to ~356 | Both old *and* new municipality names must exist |
| 2024 re-splits | Several 2020 mergers reversed | Third layer of names for same geographic area |
| Future digitisation | Old undigitised records reference any historical name | Tree must remain open and extensible |

**Strategy:**

1. **Build from MUSIT admin-place sources as-is** — do not normalise to “current” administrative names. Prefer **`ADMINISTRATIVE_PLACE`** when it is populated (`ADMPLACE_TYPE` = level, `PLACE_ID_PARTOF` = parent). In Oracle PROD checks (2026-04-15, migration reporting user), **`ADMINISTRATIVE_PLACE` was empty** while **`PLACE_HIERARCHICAL_PLACE` → `HIERARCHICAL_PLACE_OLD`** carried almost all admin names on **`PLACE`** rows; if your environment matches, import geography nodes from **`HIERARCHICAL_PLACE_OLD`** (via `HIERACHICAL_TYPE` → `TYPES`) in addition to USD. See [Oracle botany datasets — Geolocation](oracle_botany_datasets.md#geolocation-oracle-musit-usd).
2. **Supplement from USD `ADMINISTRATIVTSTED`** — each per-museum schema has its own administrative place table; add any names not already present in MUSIT. Match on name + type + parent to avoid duplicates.
3. **`GEOREG`** (the old UTM-grid-based geographic register in USD schemas) contains municipality codes (kommnr) and names. Use as a cross-reference to catch additional historical names not in MUSIT admin tables. Not every USD botany schema exposes **`GEOREG`** to the same Oracle user—discover with `ALL_TABLES`.
4. **Do not delete or merge historical nodes** — a "Trondheim" from 1900 and a "Trondheim" that is a post-2020 merged municipality may coexist in the tree. Specify's Geography tree supports this.
5. **Mark status optionally** — a custom `GeographyStatus` field (`CURRENT` / `HISTORICAL` / `MERGED_INTO`) on the `Geography` table can help users understand which nodes are current administrative units. This is optional but useful.

**Source tables and what they provide:**

| Source | Table | Content |
|---|---|---|
| MUSIT | `ADMINISTRATIVE_PLACE` | Hierarchical admin units; `ADMPLACE_TYPE` = level; `PLACE_ID_PARTOF` = parent (verify populated in your DB) |
| MUSIT | `HIERARCHICAL_PLACE_OLD` | Hierarchical admin names; `HIERACHICAL_TYPE` = level; `PLACE_ID_PARTOF` = parent; linked from **`PLACE`** via **`PLACE_HIERACHICAL_PLACE`** |
| MUSIT | `PLACE_HIERACHICAL_PLACE` | Junction: which `HIERARCH_PLACE_ID` applies to each collecting **`PLACE_ID`** |
| MUSIT | `MUSIT_NATHIST_FELLES.BIO_GEOGRAFISK_REGION` | Shared biogeographic region vocabulary; linked from **`PLACE_BIO_GEOGRAFISK_REGION`** |
| USD each schema | `ADMINISTRATIVTSTED` | Per-museum admin place table; `STED_TYPE` = level; `LAND_ID`/`FYLKE_ID`/`KOMMUNE_ID` FK chain |
| USD each schema | `GEOREG` | Old UTM-zone area register; `KOMMNR` (municipality number), `NAVN`, `LAND`/`FYLKE`/`KOMMUNE` text fields |
| USD each schema | `FYLKER` | County list with `FYLKENR` (county number) |
| USD each schema | `KOMMUNER` | Municipality list (where present) |
| USD each schema | `COUNTRIES` | Country list |

**What does NOT go in Geography tree:**
- `KOORDINATE_PLACE` → maps to Specify `Locality` (specific collecting sites with coordinates), not to Geography nodes. Localities live at Collection level; Geography nodes are shared. **`KOORDINATE_PLACE_ID` is not global** across Oracle schemas (same integer can mean different coordinates in botany vs entomology); always qualify with the owning schema.
- `INDEXED_LOCALITY`, `LOCALITY_PLACE` → also Specify `Locality`, not Geography.

**Hierarchy depth in Specify:**

Specify's default Geography ranks: `Planet → Continent → Country → State/Province → County → Municipality`. Norwegian data maps as:

| Specify rank | Norwegian equivalent | ADMPLACE_TYPE value (to confirm) |
|---|---|---|
| Continent | Kontinent | type 1 |
| Country | Land | type 2 |
| State/Province | Fylke (county) | type 3 |
| County | Kommuneregion | type 4 (if used) |
| Municipality | Kommune | type 5 |

> ⚠️ Confirm which admin model is populated: `SELECT COUNT(*) FROM MUSIT_BOTANIKK_FELLES.ADMINISTRATIVE_PLACE` vs counts on **`HIERARCHICAL_PLACE_OLD`** / **`PLACE_HIERACHICAL_PLACE`**. If `ADMINISTRATIVE_PLACE` is empty, map **`HIERACHICAL_TYPE`** (with `TYPES`) instead of `ADMPLACE_TYPE` for hierarchy levels.

> ⚠️ Geography nodes are shared across all Specify collections. Build this tree once, completely, before any specimens are migrated. All four botany museums and the zoology collection will reference the same nodes.

**Prefect implementation — `migrate_oracle_geography`**

- **Flow:** [`flows/migrate_oracle_geography.py`](../flows/migrate_oracle_geography.py) (`migrate_oracle_geography_flow`). **Deployment:** `migrate-oracle-geography-dev` in [`prefect.yaml`](../prefect.yaml).
- **Shared tree:** Sets every biology discipline’s `GeographyTreeDefID` to match **Karplanter Moser** (skips Geologi). Then imports **`HIERARCHICAL_PLACE_OLD`** into `Geography` via the **Django ORM** only (`Geography.save()` / Specify’s tree rules), wrapping each insert in **`transaction.atomic()`** so parent row locking is valid. No raw SQL post-pass on MariaDB for geography rows.
- **Municipality rank:** Adds a **Municipality** rank under **County** (or **Fylke**) when missing.
- **Locality:** For each `PLACE_ID` referenced by `PLACE_OBJECT_ROLE` or `PLACE_EVENT_ROLE`, creates one **`Locality` per discipline** that shares that treedef (same `GeographyID` where resolved). **`Locality` is discipline-scoped in Specify** — this duplicates rows by design until a later dedupe pass.
- **Bridge table:** Creates `migration_oracle_placemap` in Specify MariaDB (`source_owner`, `source_kind`, `source_id`, `specify_geography_id`, `specify_locality_id`, `specify_discipline_id`, `run_ts`) for specimen migration to join `(owner, PLACE_ID)` → `LocalityID`.
- **Report:** JSON manifest under S3 prefix `migration-reports/oracle-geography-to-specify/{timestamp}/report.json` (category `REPORT_CATEGORY_ORACLE_GEOGRAPHY_TO_SPECIFY` in [`flows/lib/migration_report_s3.py`](../flows/lib/migration_report_s3.py)); requires `S3_BUCKET` on the worker. Use `dry_run=true` (default) to inventory + plan only; `dry_run=false` to write Specify + placemap.
- **Optional purge (`purge_existing_geography_for_treedef`):** Deletes all `Geography` rows for the canonical treedef (e.g. structure-sync seed data), sets `Locality.GeographyID` to NULL where it pointed at those nodes, removes blocking `Agentgeography` rows, recreates a minimal **Earth** root, and by default **`TRUNCATE`s `migration_oracle_placemap`**. Does **not** delete `Locality` rows. Use `dry_run=true` with purge enabled to see counts only. Implementation: [`flows/lib/specify_geography_purge.py`](../flows/lib/specify_geography_purge.py).

#### Step 1.3 — Taxonomy

**Source:** NorTaxa (Artsdatabanken) as primary tree + Oracle `LATIN_NAMES` for unmatched species  
**Target:** Specify `Taxon` trees (one per Discipline: Botany, Zoology/Entomology)

**Approach: NorTaxa-first**

Rather than migrating and merging the Oracle taxonomy trees, we use **NorTaxa as the canonical tree** and add anything missing from it. This works cleanly here because:
- `LATIN_NAMES.ADB_TAXON_ID` already links Oracle names to Artsdatabanken IDs — matching is a lookup, not a fuzzy merge.
- `USD_NAT_TAXAREG` was built as a synchronised copy of Artsdatabanken data — NorTaxa *is* the authority the old system was tracking.

**Migration steps:**

1. **Import NorTaxa** into Specify as the base taxon tree (via Artsdatabanken export/API). This happens before any specimens.
2. **Match**: for each Oracle `LATIN_NAMES` record, look up `ADB_TAXON_ID` → find existing Specify `Taxon` node.
3. **No match** (taxon absent from NorTaxa): insert the taxon at the correct position in the tree and mark it with a flag (see below).
4. **Synonyms**: NorTaxa already encodes accepted name ↔ synonym relationships; link `Determination.IsCurrent` accordingly.

**Flagging non-NorTaxa taxa:**

Add a custom boolean field `IsExtraNorTaxa` (or `NorTaxaStatus` varchar) to the Specify `Taxon` table. Set it on insert for any taxon added outside the base NorTaxa import.

Two sub-categories worth distinguishing:

| Category | Example | Flag value |
|---|---|---|
| Norwegian species not yet in NorTaxa | Recently described, awaiting review | `PENDING` |
| Genuinely foreign / extra-limital species | Tropical holotypes, Arctic borderline spp. | `EXTRA_LIMITAL` |
| NorTaxa match | Any species found in ADB | _(null / unset)_ |

**Key Oracle fields:**

| Oracle | Specify |
|---|---|
| `LATIN_NAMES.LATIN_NAME` | `Taxon.Name` |
| `TAXON_CATHEGORY.TAX_CATH_CODE` | `Taxon.RankID` (map rank codes to Specify rank IDs) |
| `LATIN_NAMES.PARENT_LATIN_NAME_ID` | `Taxon.Parent` (used only for non-NorTaxa inserts) |
| `LATIN_NAMES.IS_VALID` | `Taxon.IsAccepted` |
| `AUTHORSTRINGS.AUTHORSTRING` | `Taxon.Author` |
| `LATIN_NAMES.ADB_TAXON_ID` | Primary match key against NorTaxa; store in `Taxon.GUID` |
| `LATIN_NAMES.NHM_TAXON_ID` | Secondary match key; store in custom field |

> ⚠️ NorTaxa covers Norwegian-relevant taxa. For marine and entomology collections there will be a long tail of foreign species (holotypes, Arctic material, imported specimens). Budget time for reviewing the `EXTRA_LIMITAL` tail before going live.

#### Step 1.4 — Users

**Source:** `USD_METADATA.BRUKARAR` + `BRUKERNAVN_GRUPPE` + `GRUPPE`  
**Target:** Specify `SpecifyUser` + `Agent`

1. For each row in `BRUKARAR`: create a `SpecifyUser`.
2. Match or create the corresponding `Agent` (from Step 1.1) via name/email.
3. Map `GRUPPE.MUSEUM` → Specify `Collection` access.

The Prefect flow that performs the load writes a JSON summary artifact, **`migration_report.json`** (counts, errors, and a museum-group inventory). Field definitions, S3 layout, and how to interpret dry-run vs live runs are documented in [**User migration report**](user_migration_report.md).

---

### Phase 2 — Specimen Migration (one dataset at a time)

Once Phase 1 is validated, migrate specimens collection by collection. Each iteration follows the same steps and produces a testable result in Specify.

```
For each dataset (e.g. "Karplanter TRH"):
  Step 2.1 — Collecting Events + Localities
  Step 2.2 — Collection Objects (specimens)
  Step 2.3 — Determinations (+ taxon links)
  Step 2.4 — Attachments (media from USD_FELLES)
  ── VALIDATE ──
  → proceed to next dataset
```

#### Filtering by dataset

Source filter query (example for Karplanter in Trondheim):

```sql
-- USD source
SELECT f.* 
FROM USD_BOTANIKK_TRONDHEIM.FUNNETIKETT f
JOIN USD_BOTANIKK_TRONDHEIM.HERBARIE h ON f.HERB_ID = h.HERB_ID
WHERE h.HERB_FORK = 'V'   -- 'V' = Vaskulærplanter/Karplanter

-- MUSIT source  
SELECT mo.* 
FROM MUSIT_BOTANIKK_FELLES.MUSEUM_OBJECT mo
WHERE mo.SUB_COLLECTION_ID = <karplanter_id>
```

> The actual `HERB_FORK` values and `SUB_COLLECTION_ID` values need to be confirmed from live DB. Enumerate them as the first step of each collection migration.

#### Iterating across museums

Each dataset × museum combination is a separate batch. Suggested sequence:

| Batch | Source | Specify Collection | ~Size estimate |
|---|---|---|---|
| 1 | USD_BOTANIKK_TRONDHEIM / Karplanter | Karplanter (TRH) | Pilot |
| 2 | USD_BOTANIKK_TROMSO / Karplanter | Karplanter (TMS) | |
| 3 | USD_BOTANIKK_BERGEN / Karplanter | Karplanter (BRG) | |
| 4 | USD_BOTANIKK_SVALBARD / Karplanter | Karplanter (SVA) | |
| 5 | */Mosser | Mosser | |
| 6 | */Lav | Lav | |
| … | … | … | |

For each batch, the same Specify `Collection` (e.g. "Karplanter") receives records from all four museum schemas — they all share the same taxonomy and geography nodes loaded in Phase 1.

---

## Validation Gates

Each phase and batch needs a defined validation checkpoint before proceeding.

### Phase 1 gates

| After step | Check |
|---|---|
| 1.1 Agents | Row count in Specify Agent ≈ expected; spot-check 10 known collectors by name; no duplicate agents for same person |
| 1.2 Geography | Norway hierarchy complete to municipality level; spot-check known localities |
| 1.3 Taxonomy | Tree depth/structure correct; known species findable; synonym links intact |
| 1.4 Users | All active users can log in to Specify; permissions correct per collection |

### Phase 2 gates (per dataset batch)

| Check | How |
|---|---|
| Row count | `COUNT(*)` in source vs. `COUNT(*)` in Specify for the collection |
| Null FK check | No `CollectionObject` with null `Collector`, null `Taxon`, or null `Locality` where source had data |
| Sample spot-check | Pick 20 specimens, open in Specify, verify all fields match source |
| Orphan check | No `CollectingEvent` records without linked `CollectionObject` |
| Duplicate check | No duplicate `CatalogNumber` within a collection |

---

## Handling the "Shared Data Updates" Problem

Once Phase 1 is done and Phase 2 begins, new agents or taxa might appear in later datasets that weren't present in the initial foundation load. This is expected.

**Rule:** New agents/taxa discovered during a specimen batch are added to the shared tables *on the fly* during that batch migration. The shared tables grow incrementally, but each item is only ever added once (check before insert).

This means the specimen migration flows must:
1. Look up the Agent/Taxon/Geography by stable ID (Oracle PK → Specify PK mapping table).
2. If not found: insert it, record the mapping.
3. If found: link to existing.

A **cross-reference table** (maintained in the migration environment, not in Specify) is essential:

```
oracle_to_specify_map:
  oracle_schema    VARCHAR   -- e.g. 'MUSIT_BOTANIKK_FELLES'
  oracle_table     VARCHAR   -- e.g. 'ACTOR'
  oracle_id        NUMBER    -- e.g. 12345
  specify_table    VARCHAR   -- e.g. 'Agent'
  specify_id       NUMBER    -- e.g. 67890
```

This map is the migration's "memory" — it allows any phase to look up whether an Oracle record has already been imported.

---

## Summary: What Gets Migrated When

```
Phase 1 (once, before any specimens)
├── 1.1  Agents                   ← all schemas merged and deduplicated
├── 1.2  Geography tree           ← custom built from MUSIT to preserve historical names
├── 1.3  Taxonomy tree(s)         ← NorTaxa as primary backbone + unmatched Oracle taxa
└── 1.4  SpecifyUsers             ← from USD_METADATA.BRUKARAR

Phase 2 (repeated N times, one per dataset-batch)
├── Batch 1  Karplanter / TRH     ← first pilot, most carefully validated
├── Batch 2  Karplanter / TMS
├── Batch 3  Karplanter / BRG
├── Batch 4  Karplanter / SVA
├── Batch 5  Mosser / all museums
├── Batch 6  Lav / all museums
├── Batch 7  Sopp / all museums
├── Batch 8  Alger / all museums
├── Batch 9  Marine / Marin
└── Batch N  Entomologi / …

Cross-cutting concern (maintained throughout)
└── oracle_to_specify_map         ← ID mapping table, lives in migration env
```
