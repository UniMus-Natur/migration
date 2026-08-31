---
layout: default
title: MUSIT agent name variants migration
nav_order: 8
---

# MUSIT agent name variants migration

Fill-in flow for synonymic / alternate **`PERSON_NAME`** rows that were **not** stored on Specify **`Agent`** by [**MUSIT collection agents migration**](migrate_musit_agents.md).

## Why a separate flow

Phase 1.1 (`migrate_musit_agents`) already created one Specify **`Agent`** per MUSIT **`ACTOR`**, using only the preferred name (`VALID_PERSON_NAME_ID`, else `MIN(PERSON_NAME_ID)`). Remigrating agents from scratch would be wasteful and risk churn on linked collectors/determiners.

This flow (**Phase 1.1b**) only creates **`AgentVariant`** rows on those existing agents so alternate spellings are not lost.

## Flow: `migrate_musit_agent_variants_flow`

- **Module:** `flows/migrate_musit_agent_variants.py`
- **Prefect name:** `Migrate MUSIT Agent Variants`
- **Deployment:** `migrate-musit-agent-variants-dev`
- **Prerequisite:** Agents from `migrate_musit_agents` (matched by `Agent.remarks` marker).

### Source

For each schema, every **`PERSON_NAME`** whose `PERSON_NAME_ID` is **not** the preferred/fallback name already on the Agent:

- Prefer exclusion of `ACTOR.VALID_PERSON_NAME_ID`
- Else exclude `MIN(PERSON_NAME_ID)` for that actor (same rule as the agents flow)

Schemas (whitelist): `MUSIT_BOTANIKK_FELLES`, `MUSIT_ZOOLOGI_ENTOMOLOGI`.

### Target

Specify **`AgentVariant`**:

| Field | Value |
|-------|--------|
| `agent` | Existing Agent via remarks `MUSIT-migration: ACTOR; schema=…; ACTOR_ID=…` |
| `name` | `Surname, Given Middle` (MUSIT Det/Leg style), max 255 |
| `varType` | **0** (Variant — Specify default pick list) |

### Idempotency

- Skip when the Agent already has an **`AgentVariant`** with the same `name`.
- Skip empty formatted names.
- Does not create or modify **`Agent`** rows.
- Actors with no matching Specify Agent are counted as `agents_missing` (run agents migration first).

### Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `oracle_env` | `PROD` | Oracle env prefix. |
| `dry_run` | `true` | Log only; no `AgentVariant` inserts. |
| `musit_schemas` | both schemas | Subset of the two allowed schemas. |

### Report artifact

When `S3_BUCKET` is set:

`{S3_MIGRATION_REPORTS_PREFIX}/collection-agents-musit-person-name-variants/<timestamp>/report.json`

| Field | Description |
|--------|-------------|
| `flow` | `migrate_musit_agent_variants` |
| `migration_phase` | `1.1b` |
| `oracle_person_names_extracted` | Alternate `PERSON_NAME` rows scanned |
| `agents_mapped_per_schema` | Specify agents found via MUSIT markers |
| `variants_created` | Inserted (or simulated) |
| `variants_skipped_duplicate` | Same name already on agent |
| `variants_skipped_empty` | No usable name parts |
| `agents_missing` | Oracle actor with no matching Agent |
| `errors` | Per-row failures (capped) |

## What this does **not** do

- Does **not** change how Det/Leg **display** names on specimens (still the Agent primary name).
- Does **not** link a determination/collector role to a specific variant (Specify has no such FK).
- Does **not** use VarType Label Name (4); all alternates use Variant (0). Relabel in Schema Config if desired.

## Run

```bash
prefect deployment run "Migrate MUSIT Agent Variants/migrate-musit-agent-variants-dev"
# live:
prefect deployment run "Migrate MUSIT Agent Variants/migrate-musit-agent-variants-dev" --param dry_run=false
```
