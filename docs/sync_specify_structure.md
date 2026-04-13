---
layout: default
title: Specify structure sync
nav_order: 10
---

# Specify structure sync

This page documents the **Sync Specify structure** Prefect flow (`flows/sync_specify_structure.py`, `sync_specify_structure_flow`). The flow creates the division / discipline / collection hierarchy in Specify after the initial bootstrap. It is idempotent: rows that already exist are skipped, so the flow can be re-run safely at any time.

## Background

Specify requires a fixed three-level organisational hierarchy beneath the `Institution` row before any collection data can be entered:

```
Institution
  └── Division
        └── Discipline  (has own Taxon and Geography tree defs)
              └── Collection
```

The hierarchy is described in a YAML config file and reconciled against the live database using Specify's `setup_tool` API layer (same functions used during interactive setup). The YAML lives at `config/specify_structure/unimus_natur.yaml`.

## Flow

- **Module:** `flows/sync_specify_structure.py`
- **Prefect name:** `Sync Specify structure`
- **Deployment:** `sync-specify-structure-dev`
- **Default `config_path`:** `config/specify_structure/unimus_natur.yaml`
- **Default `dry_run`:** `true`

### Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `config_path` | `config/specify_structure/unimus_natur.yaml` | Path to the structure YAML file, resolved at flow start. |
| `dry_run` | `true` | When `true`, logs what would be created without writing any rows. |

### What it does

1. **Loads** the YAML from `config_path` into a `StructureConfig` dataclass (`flows/lib/specify_structure/config.py`).
2. **Bootstraps Django** via `setup_django()` so that `specifyweb` models are importable.
3. **Calls `reconcile_structure()`** (`flows/lib/specify_structure/reconcile.py`), which iterates the YAML tree:
   - Looks up the `Institution` by name (case-insensitive).
   - For each **division**: skips if a division with the same name already exists under that institution; otherwise calls `setup_api.create_division()`.
   - For each **discipline**: disciplines are matched globally by name (Specify constraint). If missing, calls `create_discipline_and_trees_task()`, which also creates the Geography and Taxon tree definitions.
   - For each **collection**: matched by `(discipline_id, code)`. If missing, calls `setup_api.create_collection()` with `run_fix_schema_config_async=False` (synchronous schema config update, no Celery dependency).
4. **Uploads a JSON report** to S3 under `specify-structure-sync/` (when `S3_BUCKET` is set).

### Idempotency details

- Divisions are matched by name (case-insensitive) under the institution.
- Disciplines are matched globally by name — Specify does not allow two disciplines with the same name regardless of division.
- Collections are matched by `(discipline_id, code)`. The same collection code can exist in multiple disciplines.
- All matching is done before any write; skipped rows increment `*_skipped` counters in the report.

## YAML config (`config/specify_structure/unimus_natur.yaml`)

The config describes the intended hierarchy for **UniMus:Natur**:

```
institution_name: "UniMus:Natur"

divisions:
  Biology
    disciplines: Karplanter Moser (botany), Alger (botany), Lav Sopp (botany),
                 Insekter (entomology), Marine invertebrater (invertebrate zoology),
                 Pattedyr (vertebrate zoology), Fugl (vertebrate zoology),
                 Fisk og herptiler (vertebrate zoology)
  Geology/paleontology
    disciplines: Geologi (geology), Paleontologi (invertpaleo)

collections per discipline: NHM, UM, UTM, VM, NBH
```

All disciplines have the same five collection codes representing the five Norwegian university museums.

## Report artifact

When `S3_BUCKET` is set, a JSON report is uploaded to:

`{S3_MIGRATION_REPORTS_PREFIX}/specify-structure-sync/{YYYYMMDDTHHMMSSZ}/report.json`

See [Migration reports on S3](migration_s3_reports.md).

| Field | Type | Description |
|-------|------|-------------|
| `flow` | string | `sync_specify_structure`. |
| `timestamp_utc` | string | UTC run timestamp. |
| `config_path` | string | Resolved absolute path of the YAML used. |
| `dry_run` | boolean | Whether writes were skipped. |
| `divisions_created` | integer | Divisions inserted or (when `dry_run`) simulated. |
| `divisions_skipped` | integer | Divisions that already existed. |
| `disciplines_created` | integer | Disciplines inserted or simulated. |
| `disciplines_skipped` | integer | Disciplines that already existed. |
| `collections_created` | integer | Collections inserted or simulated. |
| `collections_skipped` | integer | Collections that already existed. |
| `warnings` | array | Division-discipline nesting mismatches and other non-fatal notices. |
| `errors` | array | Rows that could not be created. |

## Recorded outcome: first production load (2026-04-13)

The flow was run with `dry_run: false` against the staging MariaDB on **2026-04-13** using `config/specify_structure/unimus_natur.yaml`.

| Object | Result | Notes |
|--------|--------|-------|
| Division **Biology** | skipped | Pre-existing since 2026-01-15 (created during initial bootstrap). |
| Division **Geology/paleontology** | created | Created at ~07:29 UTC. |
| 10 disciplines | all created | Created 07:26–07:29 UTC. |
| 50 collections (5 × 10) | all created | Created 07:49–08:43 UTC. |

Summary counters:

| Field | Value |
|-------|-------|
| `divisions_created` | 1 |
| `divisions_skipped` | 1 |
| `disciplines_created` | 10 |
| `disciplines_skipped` | 0 |
| `collections_created` | 50 |
| `collections_skipped` | 0 |
| `warnings` | 0 |
| `errors` | 0 |

The resulting hierarchy in the database:

| Division | Discipline | Type |
|----------|-----------|------|
| Biology | Karplanter Moser | botany |
| Biology | Alger | botany |
| Biology | Lav Sopp | botany |
| Biology | Insekter | entomology |
| Biology | Marine invertebrater | invertebrate zoology |
| Biology | Pattedyr | vertebrate zoology |
| Biology | Fugl | vertebrate zoology |
| Biology | Fisk og herptiler | vertebrate zoology |
| Geology/paleontology | Geologi | geology |
| Geology/paleontology | Paleontologi | invertpaleo |

Each discipline has five collections: **NHM, UM, UTM, VM, NBH**.

## Running the flow

```bash
prefect deployment run "Sync Specify structure/sync-specify-structure-dev" --param dry_run=false
```

To use a different YAML file:

```bash
prefect deployment run "Sync Specify structure/sync-specify-structure-dev" \
  --param config_path=/path/to/structure.yaml \
  --param dry_run=false
```
