---
layout: default
title: Migration reports on S3
nav_order: 9
---

# Migration reports on S3

Prefect flows that write migration summaries use a **shared bucket** (`S3_BUCKET`). Report keys use a **dedicated prefix** controlled by **`S3_MIGRATION_REPORTS_PREFIX`** (default **`migration-reports`**). They do **not** use **`S3_PREFIX`** (`oracle-schema`), so migration JSON stays separate from Oracle schema snapshot artifacts.

Implementations call helpers in `flows/lib/migration_report_s3.py`.

## Layout

All JSON reports live under:

```text
{S3_MIGRATION_REPORTS_PREFIX}/{category-folder}/{YYYYMMDDTHHMMSSZ}/report.json
```

Default when `S3_MIGRATION_REPORTS_PREFIX` is unset:

```text
migration-reports/{category-folder}/{YYYYMMDDTHHMMSSZ}/report.json
```

- **`S3_MIGRATION_REPORTS_PREFIX`** — Optional. Override to nest under a team or environment segment (e.g. `prod/migration-reports`). Empty values fall back to `migration-reports`.
- **`{category-folder}`** — One segment per flow type (constant in code).
- **`{YYYYMMDDTHHMMSSZ}`** — UTC run timestamp.
- **`report.json`** — Canonical filename inside the run folder.

### Current categories

| Flow | Constant in code | Folder segment | Phase |
|------|-------------------|----------------|--------|
| [Migrate Users](user_migration_report.md) | `REPORT_CATEGORY_APP_USERS_BRUKARAR` | `application-users-usd-metadata-brukarar` | 1.4 |
| [MUSIT collection agents](migrate_musit_agents.md) | `REPORT_CATEGORY_MUSIT_COLLECTION_AGENTS` | `collection-agents-musit-actor-person-name` | 1.1 |
| [Specify structure sync](sync_specify_structure.md) | `REPORT_CATEGORY_SPECIFY_STRUCTURE_SYNC` | `specify-structure-sync` | post-bootstrap |
| [NorTaxa discipline taxon trees](nortaxa_taxon_trees.md) | `REPORT_CATEGORY_NORTAXA_DISCIPLINE_TREES` | `nortaxa-discipline-trees` | 1.3 |

**Example keys** (default prefix):

- `s3://$BUCKET/migration-reports/application-users-usd-metadata-brukarar/20260410T145733Z/report.json`
- `s3://$BUCKET/migration-reports/collection-agents-musit-actor-person-name/20260410T211100Z/report.json`
- `s3://$BUCKET/migration-reports/specify-structure-sync/20260413T072900Z/report.json`
- `s3://$BUCKET/migration-reports/nortaxa-discipline-trees/20260624T140000Z/report.json`

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

1. Add a new **`REPORT_CATEGORY_*`** string constant in `flows/lib/migration_report_s3.py` (single folder name, no extra product prefixes).
2. Build the report dict with `report_version`, `flow`, `migration_phase`, plus flow-specific fields.
3. Upload with `migration_report_s3_key(YOUR_CONSTANT, ts)` and filename `report.json`.
4. Register the path in this document.

## Historical note

Older uploads may exist under:

- `{S3_PREFIX}/user-migration/…/migration_report.json`
- `{S3_PREFIX}/musit-agent-migration/…`
- `{S3_PREFIX}/migration-reports/specify7/…/report.json` (intermediate layout)

New uploads use `{S3_MIGRATION_REPORTS_PREFIX}/…` as above.

## Troubleshooting: “nothing appeared in the bucket”

1. **`S3_BUCKET` must be set on the Prefect worker pod.** Report uploads run inside **`upload_migration_report_json_task`**. If `S3_BUCKET` is missing or blank, uploads are **skipped**. Check task logs for **`MIGRATION_REPORT_SKIP`**.
2. **In-cluster worker:** Ensure the Kubernetes `Secret` contains **`S3_BUCKET`** and S3 credentials. A local `.env` is **not** injected into the pod automatically.
3. **Look under `migration-reports/`** (or your `S3_MIGRATION_REPORTS_PREFIX`), not under `oracle-schema/…` for these reports.
4. **If upload fails**, the flow run should **fail** with a boto `ClientError` — check the run’s exception and worker logs.

After a successful upload, logs include a line like  
`Uploading … report to s3://<bucket>/<key>`.
