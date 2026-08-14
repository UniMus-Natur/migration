---
layout: default
title: MUSIT agent details fill-in
nav_order: 8
---

# MUSIT agent details fill-in

Backfill flow for person-module fields on MUSIT **`ACTOR`** that were not fully mapped when [**collection agents**](migrate_musit_agents.md) were created. Complements [**name variants**](migrate_musit_agent_variants.md).

## Why a separate flow

Phase 1.1 created Agents with preferred name + a few columns (email, DOB/DOD when present). Many person-module fields (Wikidata in URL note, address, gender, display name, dates buried in `NOTE` tags) were skipped or only truncated into remarks.

This flow (**Phase 1.1c**) updates **existing** Agents in place — no re-create.

## Flow: `migrate_musit_agent_details_flow`

- **Module:** `flows/migrate_musit_agent_details.py`
- **Prefect name:** `Migrate MUSIT Agent Details`
- **Deployment:** `migrate-musit-agent-details-dev`
- **Prerequisite:** Agents from `migrate_musit_agents` (remarks markers).

### Field mapping

| MUSIT (`ACTOR`) | Specify | Rule |
|-----------------|---------|------|
| `ACTORNAME` (display name) | `Agent.text1` | Fill if empty |
| `GROUP_MEMBER_NAMES` | `Agent.text2` | Fill if empty |
| `GENDER` | `Agent.text3` | Fill if empty |
| `INSTITUTION` | `Agent.text4` | Fill if empty |
| `URL_NOTE` (raw) | `Agent.text5` | Fill if empty |
| `URL` | `Agent.url` | Fill if empty |
| `EMAIL_ADDRESS` | `Agent.email` | Fill if empty |
| `BIRTHDATE` / `DEATHDATE` | `dateOfBirth` / `dateOfDeath` | Fill if empty |
| `<FODT>` / `<DOD>` in `NOTE` | same date fields + precision | Fill if empty (after column dates) |
| `NOTE` (full) | `Agent.remarks` (`oracle_note=…`) | Refresh note payload; keep migration marker |
| `URL_NOTE` parsed | `AgentIdentifier` (`Wikidata` / `ORCID` / `URL`) | Create if identifier not already present |
| `ADRESS` / `POSTAL_ADDRESS` / `PHONE_NUMBER` | `Address` (`address` / `address2` / `phone1`) | Create one primary address if agent has none |

Idempotent: **never overwrites** non-empty Agent fields; skips duplicate identifiers; skips address if any address already exists.

### Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `oracle_env` | `PROD` | Oracle env prefix. |
| `dry_run` | `true` | Log only. |
| `musit_schemas` | both | `MUSIT_BOTANIKK_FELLES` and/or `MUSIT_ZOOLOGI_ENTOMOLOGI`. |

### Report

`{S3_MIGRATION_REPORTS_PREFIX}/collection-agents-musit-actor-details/<timestamp>/report.json`

## Still out of scope

- **`GROUPMEMBERSHIP`** (group member links) — separate relationship migration later.
- Agent form labels for `text1`–`text5` — optional schema/form localization (Display name, Gender, …).

## Run

```bash
prefect deployment run "Migrate MUSIT Agent Details/migrate-musit-agent-details-dev"
prefect deployment run "Migrate MUSIT Agent Details/migrate-musit-agent-details-dev" --param dry_run=false
```
