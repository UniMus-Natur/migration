---
layout: default
title: NorTaxa taxon trees
nav_order: 7
---

# NorTaxa taxon trees (Specify sync)

This page documents how **NorTaxa** (Artsdatabanken) taxonomy is loaded into Specify 7, what happens on each synchronisation run, and how ongoing changes are handled. It is the operational reference for **Phase 1.3 — Taxonomy** in the [migration strategy](migration_strategy.md).

**Implementation**

| Piece | Location |
|-------|----------|
| Prefect flow | [`flows/nortaxa_discipline_trees.py`](../flows/nortaxa_discipline_trees.py) |
| API client | [`flows/lib/nortaxa_api_client.py`](../flows/lib/nortaxa_api_client.py) |
| Discipline → NorTaxa root IDs | [`flows/lib/nortaxa_discipline_root_specs.py`](../flows/lib/nortaxa_discipline_root_specs.py) |
| Merge into Specify | [`flows/lib/nortaxa_specify_merge.py`](../flows/lib/nortaxa_specify_merge.py) |
| Rank template | [`flows/lib/nortaxa_taxon_tree_ranks.json`](../flows/lib/nortaxa_taxon_tree_ranks.json) |
| Incremental changelog | [`flows/lib/nortaxa_changelog_sync.py`](../flows/lib/nortaxa_changelog_sync.py) |
| Taxon-only purge | [`flows/purge_specify_taxon_trees.py`](../flows/purge_specify_taxon_trees.py) |
| Full staging reset (incl. taxa) | [`flows/purge_specify_staging_reset.py`](../flows/purge_specify_staging_reset.py) |
| Prefect deployment | `nortaxa-discipline-trees-dev` in [`prefect.yaml`](../prefect.yaml) |

---

## Design summary

- **Authority:** NorTaxa is the canonical taxonomy for Norwegian-relevant taxa. Oracle `LATIN_NAMES.ADB_TAXON_ID` / `ADB_LATIN_NAME_ID` map to the same identifiers (see [ID model](#id-model) below).
- **Source:** NorTaxa **REST API only** (no DwC zip). Bulk load uses `DataTransfer/Export`; ongoing updates use `TaxonName/ChangeLog`.
- **Scope:** One **Specify `TaxonTreeDef` per `Discipline`**, not one global tree for the whole institution. Each discipline gets a curated **slice** of NorTaxa (union of subtree roots, optional subtract).
- **Idempotency:** Re-running the flow updates existing NorTaxa-managed rows (matched by `taxonomicserialnumber`) instead of duplicating them.
- **Naming:** Specify `Taxon.name` stores the **rank-local epithet** from NorTaxa `NameString` (e.g. `alba` for *Carex alba*). `fullname` is left null on insert and rebuilt by Specify’s `set_fullnames` after each batch.
- **Synonyms:** Accepted taxa are inserted first; synonym rows are inserted then linked via `Taxon.acceptedtaxon` (Specify synonymy semantics).

---

## Architecture

```text
NorTaxa API                          Specify 7
───────────                          ─────────
DataTransfer/Export  ──extract──►   TSV per discipline (artifact)
  ?scientificNameId={root}              │
                                        ▼
                                   ensure_discipline_taxon_tree()
                                     • TaxonTreeDef (if missing)
                                     • Taxontreedefitem ranks (Life → Species …)
                                     • root Taxon "Life"
                                        │
                                        ▼
                                   merge_nortaxa_tsv_into_discipline_tree()
                                     • accepted taxa → insert / update
                                     • synonym taxa → insert / link
                                     • orphans → yesno1=false
                                        │
                                        ▼
TaxonName/ChangeLog  ──incremental──► sync_nortaxa_changelog()
  (watermarked cursor)                  • auto-apply safe changes
                                        • queue Merge/Split/Delete for review
```

Each Specify **`Discipline`** row with a mapped name (see [Discipline slices](#discipline-slices)) participates in the flow. Disciplines without a NorTaxa slice (e.g. **Geologi**) are skipped with `skip_reason=no_nortaxa_tree`.

If two disciplines share the same `TaxonTreeDefID`, only the **first** discipline’s TSV is merged in a single run (`treedef_already_merged_this_run`); this is unusual in the current UniMus:Natur layout.

---

## ID model

NorTaxa exposes two stable identifiers. Both are stored on Specify `Taxon`:

| NorTaxa / DwC field | Meaning | Specify field | Example |
|---------------------|---------|---------------|---------|
| `scientificNameId` / `taxonID` | Scientific **name** id (Oracle `ADB_TAXON_ID`) | `taxonomicserialnumber` | `1158` |
| `taxonId` | **Taxon concept** id (for changelog correlation) | `text2` | concept uuid / numeric id |

**Provenance fields** on NorTaxa-managed taxa:

| Field | Value |
|-------|--------|
| `source` | `"NorTaxa"` |
| `text1` | Last sync stamp (UTC ISO datetime of the flow run) |
| `yesno1` | `true` if the name appears in the **current** export slice; `false` if **orphaned** (was NorTaxa-managed but no longer in the slice) |

**Specimen migration** should resolve determinations by `taxonomicserialnumber` (= `ADB_TAXON_ID`), not by binomial string matching.

---

## Field mapping (API → Specify)

| API / TSV column | Specify `Taxon` |
|------------------|-----------------|
| `scientificName` (`NameString`) | `name` (epithet) |
| _(computed)_ | `fullname` via `set_fullnames` |
| `scientificNameAuthorship` | `author` |
| `vernacularNameBokmaal` | `commonname` |
| `taxonRank` | `definitionitem` / `rankid` (via `Taxontreedefitem`) |
| `taxonomicStatus` | drives accepted vs synonym handling |
| `parentNameUsageID` | `parent` (after parent row exists in tree) |
| `acceptedNameUsageID` | `acceptedtaxon` (synonyms only) |
| `taxonID` | `taxonomicserialnumber` |
| `taxonId` | `text2` |

Rows with `taxonomicStatus` other than **Accepted** or **Synonym** (e.g. `Unresolved`) are skipped.

Rank alias: NorTaxa **Form** → Specify rank item **Forma**.

---

## Discipline slices

Root `scientificNameId` values are hard-coded in [`nortaxa_discipline_root_specs.py`](../flows/lib/nortaxa_discipline_root_specs.py). Matching is **exact** on `Discipline.name` as stored in Specify.

| Discipline name | NorTaxa slice (summary) |
|-----------------|-------------------------|
| Karplanter Moser | Bryophyta + vascular plant phyla (Magnoliophyta, Pinophyta, …) |
| Alger | Chromista + Chlorophyta roots |
| Lav Sopp | Kingdom Fungi |
| Insekter | Class Insecta |
| Marine invertebrater | Animalia minus Chordata subtree |
| Pattedyr | Mammalia |
| Fugl | Aves |
| Fisk og herptiler | Fish + amphibians + reptiles (+ related vertebrate classes) |
| Paleontologi | Fossil-relevant kingdom-level union |
| Geologi | _(skipped — no biological tree)_ |

**Subtract logic:** e.g. Marine invertebrater unions Animalia (`1`) then subtracts Chordata (`196`).

Re-verify root IDs after major NorTaxa checklist releases if coverage looks wrong.

---

## Prefect flow: three phases

Deployment: **`nortaxa-discipline-trees-dev`** (`dry_run: true` by default).

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `dry_run` | `true` | `false` required to write to Specify |
| `run_changelog_sync` | `true` | Run incremental changelog after bulk merge |
| `changelog_from_date` | _(watermark)_ | Override changelog start cursor |
| `changelog_watermark_path` | `data/nortaxa-changelog-state.json` | Persisted cursor between runs |
| `api_base_url` | `https://nortaxa.artsdatabanken.no` | NorTaxa API base |
| `output_parent` | `migration/data` | Local TSV artifact directory |

### Phase 1 — Extract

For each mapped discipline, the flow calls `DataTransfer/Export` per root `scientificNameId`, unions rows by `taxonID`, applies subtract subtrees, and writes a TSV under `data/nortaxa-discipline-trees/{timestamp}/`.

### Phase 2 — Bootstrap + merge

For each discipline artifact:

1. **`ensure_discipline_taxon_tree`**
   - Creates `TaxonTreeDef` and links `Discipline.TaxonTreeDefID` if missing.
   - Creates rank-0 **Life** `Taxontreedefitem` and root `Taxon` if missing.
   - Creates all other ranks from [`nortaxa_taxon_tree_ranks.json`](../flows/lib/nortaxa_taxon_tree_ranks.json) (Kingdom … Forma) and chains `parent` links.

2. **`merge_nortaxa_tsv_into_discipline_tree`**
   - **Pass 1 — accepted:** insert missing taxa (parent order), refresh existing (`present_refreshed`), update parent/name/author/vernacular when API drifts.
   - **Pass 2 — synonyms:** insert missing synonym rows, then set `acceptedtaxon` and `isaccepted=false` (updates `Determination.preferredtaxon` where needed).
   - **Orphans:** NorTaxa-sourced taxa in Specify but not in the current export get `yesno1=false`.
   - **`set_fullnames`** on the tree def after the batch.

All `Taxon.save()` calls run inside `transaction.atomic()` because Specify’s tree save uses `select_for_update()`.

### Phase 3 — Changelog sync (optional)

Polls `TaxonName/ChangeLog` from the watermark cursor (or `changelog_from_date`, or `2020-01-01` on first run). Only events touching taxa already in the merged tree defs are in scope, except new **TaxonCreated** / **Inserted** events which may add taxa.

---

## What happens on synchronisation (scenarios)

### First load (empty Specify taxon trees)

1. Tree def + full rank ladder + Life root created per discipline.
2. Entire discipline slice inserted (accepted, then synonyms).
3. Changelog watermark advanced if `dry_run=false`.
4. Expect large `inserted` counts; `skipped_unknown_rank` should be **0** (if not, ranks failed to bootstrap — see [Troubleshooting](#troubleshooting)).

### Re-run (trees already populated)

| Situation | Behaviour |
|-----------|-----------|
| Taxon already in Specify (`taxonomicserialnumber` match) | Row refreshed (`text1`, `yesno1=true`); name/author/parent updated if API changed |
| New taxon in NorTaxa export | Inserted |
| Taxon removed from NorTaxa slice | **Not deleted**; marked orphan (`yesno1=false`) |
| Parent changed in NorTaxa | `Taxon.parent` updated on re-merge |
| Synonym newly in export | Inserted and linked to accepted |
| Synonym link changed | `acceptedtaxon` updated |

The flow is safe to re-run after partial failure (e.g. crash during synonym linking).

### Dry run (`dry_run=true`)

- API extract and TSV artifacts are still written.
- If a discipline needs a **new** tree def, rank items, or root, merge is **skipped** for that discipline (`dry_run_tree_bootstrap_needed`) — bootstrap is not simulated row-by-row.
- If the tree already exists with ranks, merge runs in dry-run mode (counts only, no DB writes).
- Changelog sync respects `dry_run` (no watermark advance).

### Shared treedef in one run

If discipline A and B point at the same `TaxonTreeDefID`, only the first discipline’s TSV is merged; the second logs `treedef_already_merged_this_run`. Fix discipline ↔ treedef linkage if that is unintended.

---

## Incremental changelog scenarios

### Auto-applied (no curator action)

| `changeType` | Action in Specify |
|--------------|-------------------|
| `TaxonCreated`, `Inserted` | Insert taxon if missing (same rules as bulk merge) |
| `ParentChange` | Update `Taxon.parent` |
| `AuthorChanged` | Update `Taxon.author` |
| `ValidNameChange`, `Swap` | Old accepted name → synonym of new accepted (`acceptedtaxon`); determinations repointed |

After changelog processing, **`set_fullnames`** runs on affected tree defs.

### Queued for curator review (report only)

| `changeType` | Why not auto-applied |
|--------------|----------------------|
| `Merge` | Combining taxon concepts may affect many determinations |
| `Split` | One concept becoming several needs manual tree surgery |
| `Delete` | Removing names/concepts needs human confirmation |

Review items are included in the flow manifest / S3 report with:

- `changeType`, `changeDate`, `changeByUser`, `changeRemarks`
- affected `scientificNameIds` / `taxonIds`
- `determination_count` for names in the current Specify tree
- raw `dataBefore` / `dataAfter`

**Expected future process:** curators resolve Merge/Split/Delete in NorTaxa or Specify, then a later bulk re-merge or manual Specify tree tools align the database.

### Out-of-scope changelog events

Events for taxa **not** in the current Specify trees are ignored (except `TaxonCreated` / `Inserted`, which may add new rows). This keeps incremental sync bounded to imported slices.

---

## Future scenarios (planned / expected)

### Oracle specimen migration (Phase 2)

When loading determinations from `LATIN_NAMES`:

1. Look up Specify `Taxon` by `taxonomicserialnumber` = `ADB_TAXON_ID`.
2. If **found** → link determination to that node.
3. If **not found** → insert a supplementary taxon (see below) or flag for review.

### Taxa absent from NorTaxa (`EXTRA_LIMITAL` / `PENDING`)

NorTaxa does not cover every name in legacy Oracle (foreign holotypes, Arctic edge cases, undescribed taxa). Planned approach (see [migration strategy](migration_strategy.md)):

| Category | Example | Planned flag |
|----------|---------|----------------|
| Awaiting NorTaxa inclusion | Recently described Norwegian species | `PENDING` |
| Genuinely extra-limital | Tropical species in collection | `EXTRA_LIMITAL` |
| NorTaxa match | Normal case | _(unset)_ |

These inserts are **not** implemented in the NorTaxa flow yet; they will use Oracle parent links and custom status fields. NorTaxa-managed rows remain identifiable via `source='NorTaxa'`.

### NorTaxa checklist updates

| Event | Expected handling |
|-------|-------------------|
| New species in slice | Next bulk merge or changelog `TaxonCreated` inserts |
| Name change (accepted) | Changelog `ValidNameChange` / `Swap` → auto-synonym old name |
| Rank / parent fix | Bulk re-merge or changelog `ParentChange` |
| Merge / split / delete in NorTaxa | Review queue → curator action |
| Root ID change in NorTaxa | Update [`nortaxa_discipline_root_specs.py`](../flows/lib/nortaxa_discipline_root_specs.py), re-run merge |

### Manual curator edits in Specify

The sync **overwrites** NorTaxa-managed fields on re-merge (`name`, `author`, `parent`, `acceptedtaxon` when API drifts). Curators should treat NorTaxa as authority for rows with `source='NorTaxa'`, or change `source` after deliberate local overrides.

### Production scheduling

Expected steady state after initial load:

1. **Scheduled** `nortaxa-discipline-trees-dev` with `dry_run=false` (frequency TBD — weekly or after NorTaxa releases).
2. Monitor S3 report for `review_items` and merge stats (`skipped_unknown_rank`, `skipped_missing_parent`).
3. Optional: changelog-only runs if bulk export becomes too heavy (would require a separate deployment parameterisation).

---

## Staging purge (reset taxon data)

| Deployment | Effect |
|------------|--------|
| `purge-specify-taxon-trees-dev` | Truncates all taxon tables; unlinks `Discipline.TaxonTreeDefID`; keeps agents and users |
| `purge-specify-staging-reset-dev` | Above + specimens, geography, localities (full staging wipe except users/agents) |

Typical dev sequence:

1. `purge-specify-staging-reset-dev` (`dry_run=false`)
2. `nortaxa-discipline-trees-dev` (`dry_run=false`)
3. Spot-check a known species (e.g. *Carex alba*: `name=alba`, `fullname=Carex alba`)

---

## Reports and artifacts

| Output | Location |
|--------|----------|
| TSV slices | `migration/data/nortaxa-discipline-trees/{timestamp}/taxon-discipline-*.tsv` |
| Changelog watermark | `migration/data/nortaxa-changelog-state.json` |
| S3 report | `migration-reports/nortaxa-discipline-trees/{timestamp}/report.json` |

Merge summary fields in the report:

| Field | Meaning |
|-------|---------|
| `inserted` | New accepted taxa |
| `synonyms_inserted` | New synonym rows |
| `synonyms_linked` | Synonym → accepted links applied |
| `present_refreshed` | Existing slice taxa touched |
| `orphans_marked` | `yesno1=false` |
| `skipped_unknown_rank` | Rank not in `Taxontreedefitem` — should be 0 after rank bootstrap |
| `skipped_missing_parent` | Accepted rows waiting on missing parent |
| `parents_updated` | Parent changes applied |
| `errors` | Per-row failures (capped in manifest) |

Changelog summary: `events_seen`, `auto_applied`, `review_queued`, `review_items`.

---

## Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `inserted=0`, high `skipped_unknown_rank` | Tree def has only Life rank | Ensure `ensure_taxon_tree_rank_items` ran; check `rank_items.created_rank_items` in bootstrap |
| `TransactionManagementError: select_for_update` | `Taxon.save()` outside transaction | Fixed in current code — redeploy worker image / flow code |
| `dry_run_tree_bootstrap_needed` | New discipline, `dry_run=true` | Run once with `dry_run=false` to create tree |
| `unmapped_discipline_name` | `Discipline.name` not in root specs | Add mapping or rename discipline in Specify |
| Empty TSV / missing roots | Stale `scientificNameId` | Update root specs; check API export manually |
| Duplicate taxa | Same serial, different parent | Should not happen if `taxonomicserialnumber` is unique per tree; investigate manual duplicates |

**Validation spot-checks** (Phase 1 gate):

- Known species findable by serial id and full name
- Species epithet in `name`, binomial in `fullname`
- Synonym points at accepted via `acceptedtaxon`
- Tree depth sensible for discipline (plants vs insects)

---

## Related documentation

- [Migration strategy — Phase 1.3](migration_strategy.md#step-13--taxonomy)
- [Oracle botany datasets — taxon linkage](oracle_botany_datasets.md)
- [Migration reports on S3](migration_s3_reports.md)
