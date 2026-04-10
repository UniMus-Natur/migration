---
layout: default
title: MUSIT collection agents migration
nav_order: 8
---

# MUSIT collection agents migration

This page documents how **collection agents** in Oracle relate to **application users**, what the **Migrate MUSIT Actors** Prefect flow does, and how it differs from [**User migration report / BRUKARAR flow**](user_migration_report.md).

## Users vs agents in Oracle (and Specify)

MUSIT keeps two largely separate notions of “people”:

| Concept in MUSIT | Typical Oracle source | Role | Specify target |
|------------------|------------------------|------|------------------|
| **Application user** (login) | `USD_METADATA.BRUKARAR` (+ groups) | Staff who sign into the apps | `SpecifyUser` + linked `Agent` for attribution |
| **Collection agent** (specimen graph) | `MUSIT_*`.`ACTOR` + `PERSON_NAME` (+ USD `PERSONER` / authors, not in this flow yet) | Collectors, determiners, organisations on events | `Agent` only (`SpecifyUser` usually null) |

The same person may appear in **both** `ACTOR` and `BRUKARAR`. Specify also uses **`Agent`** for both login persons and specimen roles, so over time you may want to **merge** duplicates by name or email; that merge is **not** implemented in the current flows.

Strategic background and table lists: [**Oracle Schema Overview — Persons, Agents & Users**](oracle_schema_overview.md#persons-agents--users). Phased order: [**Migration Strategy — Step 1.1 vs 1.4**](migration_strategy.md#step-11--agents).

## Flow: `migrate_musit_agents_flow`

- **Module:** `flows/migrate_musit_agents.py`
- **Prefect name:** `Migrate MUSIT Actors`
- **Phase:** 1.1 (shared **collection** `Agent` rows before specimen migration).

### Implementation note (memory and Prefect)

Oracle returns on the order of **hundreds of thousands** of `ACTOR` rows. The flow uses a **single Prefect task** (`extract_and_load_musit_agents_task`) that streams rows from Oracle and applies the load in the same process, instead of returning a giant Python list across a task boundary (which would force Prefect to **serialize** the full result and roughly **double memory** use). **Dry-run** progress is logged at **INFO** every 5 000 rows; per-row lines are **DEBUG** only so the worker is not flooded with hundreds of thousands of log events (which can overwhelm log backends and look like a crash). The dev worker pod has a **1 Gi** memory limit in the default chart; if live (`dry_run: false`) runs OOM, raise `prefect.devWorker.resources.limits.memory` in Helm.

### Source

For each selected schema, the flow reads **`ACTOR`** and joins **`PERSON_NAME`**:

- Prefer **`ACTOR.VALID_PERSON_NAME_ID`** when set.
- Otherwise use the **minimum `PERSON_NAME_ID`** for that `ACTOR_ID` (deterministic fallback when there is no valid flag).

Allowed schema names (whitelist inside the flow):

- `MUSIT_BOTANIKK_FELLES`
- `MUSIT_ZOOLOGI_ENTOMOLOGI`

### Target

Specify **`Agent`** via Django ORM:

- **`agenttype`:** MUSIT `ACTOR_TYPE` **0 (person)** → Specify **1 (Person)**; MUSIT **1 (organisation)** or **2 (group)** → Specify **0 (Organization)**.
- **Names:** `PERSON_GIVEN_NAME` / `PERSON_SURNAME` (truncated to Specify column limits). If a person has no name parts, **`ACTORNAME`** is used as last name. Organisations use **`ACTORNAME`** as last name when surname is empty.
- **Other fields:** email, title, middle name, birth/death dates, division (first `Division` in the DB, same pattern as `migrate_users`).
- **`specifyuser`:** always **null** here (these are not login rows).
- **Idempotency:** `remarks` is set to a fixed marker  
  `MUSIT-migration: ACTOR; schema=<SCHEMA>; ACTOR_ID=<id>`  
  An existing `Agent` with the **same `remarks`** is **skipped** on re-run.

### Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `oracle_env` | `PROD` | Oracle env prefix for credentials (`ORACLE_<ENV>_…`). |
| `dry_run` | `true` | If `true`, only logs intended creates; no inserts. |
| `musit_schemas` | both schemas | JSON list of schema names to include (subset of the two allowed values). |

### Report artifact

When `S3_BUCKET` is set, a JSON summary is uploaded to:

`{S3_MIGRATION_REPORTS_PREFIX}/collection-agents-musit-actor-person-name/<timestamp>/report.json`

(Default prefix: `migration-reports` — see [**Migration reports on S3**](migration_s3_reports.md); not under `oracle-schema`.)

The payload mirrors the user-migration style: shared metadata (`report_version`, `flow`, `migration_phase`, `generated_at_utc`, `oracle_env`, `dry_run`) plus flow-specific counts and diagnostics. See [**Migration reports on S3**](migration_s3_reports.md).

| Field | Type | Description |
|--------|------|-------------|
| `report_version` | integer | JSON shape version (`1`). |
| `flow` | string | `migrate_musit_agents`. |
| `migration_phase` | string | `1.1`. |
| `generated_at_utc` | string | UTC folder timestamp. |
| `oracle_env` | string | Oracle env prefix. |
| `dry_run` | boolean | No `Agent` rows written when `true`. |
| `musit_schemas` | array of strings | Schemas included in the run. |
| `oracle_actors_extracted` | integer | Rows returned from Oracle for all schemas. |
| `oracle_rows_per_schema` | object | Count of Oracle rows per schema key. |
| `oracle_actor_type_counts` | object | Raw MUSIT `ACTOR_TYPE` counts (keys `"0"`, `"1"`, `"2"`, `"null"`). |
| `agents_created` | integer | Agents inserted or simulated. |
| `agents_skipped` | integer | Already present (matching `remarks` marker). |
| `agents_linked` | integer | Always `0` here (no `SpecifyUser` on these agents). |
| `schemas_processed` | array | Distinct schemas seen while loading. |
| `errors` | array of strings | Per-row failures. |

## What this flow does **not** do yet

- **USD `PERSONER` / `LEGSAMLER` / `DETBESTEMMER` / `AUTORPERSON`** — still per the strategy doc; separate extract/merge logic is planned for later.
- **`AUTHORSTRINGS`** / taxonomic author abbreviation — not joined in this version.
- **Cross-schema deduplication** — the same human could exist in botany and entomology `ACTOR` with different IDs; both rows become two Specify agents unless you merge manually or extend the flow.
- **Linking to `SpecifyUser`** created by **`migrate_users`** — user migration still creates its own `Agent` per login; reconciling login `Agent` with an existing MUSIT `ACTOR` `Agent` is future work.

## Deployment

Registered in `prefect.yaml` as **`migrate-musit-agents-dev`** (see that file for `work_pool` / parameters). Run from the CLI with `PREFECT_API_URL` pointed at your server, for example:

```bash
prefect deployment run "Migrate MUSIT Actors/migrate-musit-agents-dev" --param dry_run=false
```

## Recommended order

For a **greenfield** database, run **collection agents** (`migrate_musit_agents_flow`) **before** application users (`migrate_users_flow`) if you later want a single `Agent` per person across specimens and logins. If users were migrated first, you may temporarily have **two** `Agent` rows for some staff until a merge pass is defined.
