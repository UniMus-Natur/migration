---
layout: default
title: Migration reports on S3
nav_order: 9
---

# Migration reports on S3

Prefect flows that write migration summaries use a **shared bucket** (`S3_BUCKET`) and a **shared layout** under your optional prefix (`S3_PREFIX`, default `oracle-schema`). Implementations call helpers in `flows/lib/migration_report_s3.py`.

## Layout

All JSON reports live under:

```text
{S3_PREFIX}/migration-reports/{category-path}/{YYYYMMDDTHHMMSSZ}/report.json
```

- **`migration-reports`** — Single umbrella folder for anything that is a “migration run result” (easy to browse, lifecycle, and IAM).
- **`{category-path}`** — Human-readable path describing **target system** + **source** (not a random code name).
- **`{YYYYMMDDTHHMMSSZ}`** — UTC run timestamp (same format as before).
- **`report.json`** — One canonical filename per run so tools can always fetch the same object name inside the run folder.

### Current categories

| Flow | `category-path` constant (in code) | Phase |
|------|-------------------------------------|--------|
| [Migrate Users](user_migration_report.md) | `specify7/application-users-usd-metadata-brukarar` | 1.4 |
| [MUSIT collection agents](migrate_musit_agents.md) | `specify7/collection-agents-musit-actor-person-name` | 1.1 |

**Example keys** (prefix `oracle-schema`, time `20260410T150000Z`):

- `s3://$BUCKET/oracle-schema/migration-reports/specify7/application-users-usd-metadata-brukarar/20260410T150000Z/report.json`
- `s3://$BUCKET/oracle-schema/migration-reports/specify7/collection-agents-musit-actor-person-name/20260410T150000Z/report.json`

## Report JSON conventions

Each report is a single JSON object. Shared metadata (where applicable):

| Field | Meaning |
|--------|---------|
| `report_version` | Integer; bump when incompatible shape changes. |
| `flow` | Stable flow id (`migrate_users`, `migrate_musit_agents`, …). |
| `migration_phase` | Strategy phase string (`1.1`, `1.4`, …). |
| `generated_at_utc` | Same as folder timestamp. |
| `oracle_env` | Oracle credential profile (`PROD`, `TEST`, …). |
| `dry_run` | Whether Specify rows were only simulated. |

Flow-specific counters and arrays follow (see the per-flow docs).

## Adding a new flow

1. Add a new path constant under `MIGRATION_REPORTS_ROOT` in `flows/lib/migration_report_s3.py` (descriptive `specify7/…` or future top-level segment).
2. Build the report dict with `report_version`, `flow`, `migration_phase`, plus flow-specific fields.
3. Upload with `migration_report_s3_key(prefix, YOUR_CONSTANT, ts)` and filename `report.json`.
4. Register the path in this document.

## Historical note

Older runs may still exist under `{S3_PREFIX}/user-migration/…/migration_report.json` or `{S3_PREFIX}/musit-agent-migration/…`. New uploads use the layout above.

## Troubleshooting: “nothing appeared in the bucket”

1. **`S3_BUCKET` must be set where the flow runs.** If it is missing or blank, uploads are **skipped** (the flow still completes). The Prefect UI result includes `report_uploaded: false` and an empty `uploaded` list. Check **flow run logs** for a warning starting with `S3_BUCKET is not set`.
2. **In-cluster worker:** The dev worker loads env from `secrets.existingSecret` (`envFrom`). Ensure that Kubernetes `Secret` contains **`S3_BUCKET`** (and credentials such as `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` or `AWS_*`, plus `S3_ENDPOINT_URL` if you use MinIO). A local `.env` on your laptop is **not** injected into the pod automatically.
3. **Look under the new prefix:** Objects are under  
   `{S3_PREFIX}/migration-reports/specify7/…/<timestamp>/report.json`  
   not the old `user-migration/` paths.
4. **If upload fails** (permissions, network policy, wrong endpoint), the flow run should **fail** with a boto `ClientError` — check the run’s exception and worker logs.

After a successful upload, logs include a line like  
`Uploading … report to s3://<bucket>/<key>`.
