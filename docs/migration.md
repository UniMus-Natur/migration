---
layout: default
title: ETL implementation
nav_order: 5
---

# ETL implementation

Specimen and reference data move from MUSIT Oracle into the staging MariaDB through Prefect flows that write with the Specify 7 Django ORM.

## What runs

| Step | Flow | Doc |
|---|---|---|
| Agents, variants, details | `migrate-musit-agents-full-dev` and the split agent flows | [Agents](migrate_musit_agents.md) |
| Specify divisions, disciplines, collections | `sync-specify-structure-dev` | [Structure sync](sync_specify_structure.md) |
| Taxon trees | `nortaxa-discipline-trees-dev` | [NorTaxa](nortaxa_taxon_trees.md) |
| Application users | `migrate-users-dev` | [User report](user_migration_report.md) |
| Vascular specimens | one deployment per collection in `flows/migrate_oslo_vascular.py` | [Strategy](migration_strategy.md), [Prefect runbook](prefect_worker.md) |

Geography nodes and localities are created while a specimen flow runs (`flows/lib/oracle_geography_load.py`, `flows/lib/musit_coordinate_map.py`). Coordinates: [MUSIT coordinate migration](musit_coordinate_migration.md).

Oracle access from the cluster uses `python-oracledb` in thick mode. Connectivity check: `infrastructure-prod-check-dev`.

## Staging and test

Staging is the Specify instance on NIRD (`https://specify-stg.natur.unimus.no/`). The test instance is not deployed by this chart. Its database is replaced from staging by `sync-specify-db-to-test-dev` (SSH tunnel on the prefect-dev-worker) after schema fingerprints match. See the [Prefect runbook](prefect_worker.md#sync-staging-db--test-ssh-tunnel).
