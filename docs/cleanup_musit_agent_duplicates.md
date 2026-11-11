---
layout: default
title: Cleanup MUSIT agent duplicates
nav_order: 9
---

# Cleanup MUSIT agent duplicates

One-shot repair for staging after `migrate_musit_agent_details` stripped
`schema` / `ACTOR_ID` from Agent remarks and a later
`update_existing_names` run created unused duplicates.

## What went wrong

1. Agents were created with marker  
   `MUSIT-migration: ACTOR; schema=…; ACTOR_ID=…`
2. Agent-details rewrote remarks to  
   `MUSIT-migration: ACTOR; oracle_note=…` (marker lost)
3. Name update matched on the **exact** marker → missed those Agents →
   created new Agents with a proper marker and fuller names
4. Orphans kept AgentVariant / address / identifier rows; new dupes had none

## Flow: `cleanup_musit_agent_duplicates_flow`

- **Module:** `flows/cleanup_musit_agent_duplicates.py`
- **Prefect name:** `Cleanup MUSIT Agent Duplicates`
- **Deployment:** `cleanup-musit-agent-duplicates-dev`

For each Oracle `ACTOR`:

1. Collect candidates: Agents with a parseable marker for that
   `schema`+`ACTOR_ID`, plus oracle_note-only orphans whose `text1`
   matches `ACTORNAME`
2. Keep the richest Agent (variants → addresses → identifiers → oldest id)
3. Restore full remarks marker (preserving `oracle_note` / institution)
4. Refresh name fields from `ACTORNAME`
5. Reassign related rows from dupes onto the keeper, then delete dupes
   (skipped if collector/determiner/cataloger/etc. FKs exist)

### Parameters

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `oracle_env` | `PROD` | Oracle env prefix |
| `dry_run` | `true` | Log only; no writes |
| `musit_schemas` | both | Schema whitelist |

## How to run

```bash
# After push + prefect deploy --all

# 1) Dry-run first
prefect deployment run 'Cleanup MUSIT Agent Duplicates/cleanup-musit-agent-duplicates-dev'

# 2) Live
prefect deployment run \
  'Cleanup MUSIT Agent Duplicates/cleanup-musit-agent-duplicates-dev' \
  --param dry_run=false
```

Optional follow-up (names on any remaining marker-matched Agents):

```bash
prefect deployment run \
  'Migrate MUSIT Actors/migrate-musit-agents-dev' \
  --param dry_run=false \
  --param update_existing_names=true
```

## Related fixes

- `musit_agent_details` now always rebuilds remarks from `schema`+`ACTOR_ID`
  when writing `oracle_note=`
- `migrate_musit_agents` matches markers with trailing extras
  (`institution` / `oracle_note`), not only exact equality
