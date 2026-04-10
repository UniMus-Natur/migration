---
layout: default
title: User migration report
nav_order: 7
---

# User migration report (`migration_report.json`)

This document describes the JSON artifact written by the Prefect flow **Migrate Users** (`flows/migrate_users.py`, `migrate_users_flow`). That flow implements **Phase 1.4** in the phased strategy: loading application users from Oracle `USD_METADATA` into Specify 7 as `SpecifyUser` and `Agent` records. For the overall plan, see [Migration Strategy (Phased)](migration_strategy.md). Collection-side people (`ACTOR` / collectors) are a different source; see [MUSIT collection agents migration](migrate_musit_agents.md).

## Purpose

The report captures **run metadata**, **aggregate counts**, **per-user error messages** (if any), and an **inventory of Oracle group names grouped by museum**. It is intended for auditing a run and for later design work mapping legacy groups to Specify collection access (the flow does not assign Specify permissions from groups today).

## How it is produced

1. **Extract** — Users, memberships, and groups are read from `USD_METADATA.BRUKARAR`, `BRUKERNAVN_GRUPPE`, and `GRUPPE`.
2. **Load** — For each Oracle user, the flow either skips (existing `SpecifyUser` with the same username), simulates creation (`dry_run=True`), or creates `SpecifyUser` and `Agent` via the Django ORM (`dry_run=False`).
3. **Report** — The flow builds a JSON object and, when `S3_BUCKET` is set (non-empty), uploads it with `upload_file_with_compat_retry` to object storage. If `S3_BUCKET` is unset, nothing is uploaded; the Prefect run return value still includes the same counters and error list, plus **`report_uploaded`: `false`** and an empty **`uploaded`** list (see flow logs for a warning).

S3 object key (timestamp is UTC at upload time; shared layout with other flows):

- `{S3_PREFIX}/migration-reports/specify7/application-users-usd-metadata-brukarar/{YYYYMMDDTHHMMSSZ}/report.json`
- Default `S3_PREFIX` if unset: `oracle-schema`

See [**Migration reports on S3**](migration_s3_reports.md) for the full convention and how to add future flows.

A copy saved under `schemas/migration_report.json` is useful locally but is **gitignored**; treat any checked-out path as a generated sample, not source of truth.

## JSON fields

| Field | Type | Description |
|--------|------|-------------|
| `report_version` | integer | Schema version for this JSON shape (currently `1`). |
| `flow` | string | Always `migrate_users` for this artifact. |
| `migration_phase` | string | Strategy phase (`1.4`). |
| `generated_at_utc` | string | UTC timestamp when the report file was written, format `YYYYMMDDTHHMMSSZ`. |
| `oracle_env` | string | Flow parameter (e.g. `PROD`, `TEST`) selecting Oracle connection settings from the environment. |
| `dry_run` | boolean | `true`: no `SpecifyUser`/`Agent` rows are persisted; counts reflect what **would** be created for users not already present. `false`: creates are attempted. |
| `users_created` | integer | In dry run, one increment per Oracle user that would be created. In a live run, users actually inserted. |
| `users_skipped` | integer | Oracle users skipped because a `SpecifyUser` with the same `name` (username) already exists. |
| `agents_created` | integer | In dry run, incremented together with `users_created`. In a live run, `Agent` rows created. |
| `agents_linked` | integer | Agents saved with `specifyuser` set. Stays `0` when `dry_run` is `true`. |
| `errors` | array of strings | One entry per failed user during a live run (exception message includes the Oracle username). Empty if none. |
| `group_to_museum_mapping` | array | See below. |

### `group_to_museum_mapping`

Each element has:

- `museum` — Value from `GRUPPE.MUSEUM` for those groups, or the literal `(none)` if the column is empty.
- `groups` — Sorted list of **distinct** `GRUPPE.NAVN` values seen while iterating **all** extracted Oracle users (not only users that were created).

So this block is a **catalogue of legacy group labels by museum**, not a record of Specify permissions. Expect:

- **Functional groups** (administration, curation read/write, conservation, magazine access, etc.) alongside **project- or campaign-style** names.
- **Uneven group counts per museum** — reflects how grouping was used historically, not user counts.
- **Same display name under multiple museums** possible when Oracle has separate group rows per museum; permission mapping should use stable Oracle keys where available, not names alone.

## Reading a report

- **`users_created` == `agents_created` and `agents_linked` == 0` with `dry_run: true`** — Normal for a successful dry run: every non-skipped user would get one agent, nothing is linked in the database yet.
- **`users_skipped` > 0** — Target Specify DB already contained some usernames; re-runs are idempotent for those accounts.
- **`errors` non-empty** — Inspect messages; usernames appear in the formatted error strings from the flow.
- **`users_created` > `agents_created` on a live run** — Can happen when `SpecifyUser.save()` succeeds but `Agent.save()` raises (for example column length limits on name or email). Django saves are not wrapped in a single database transaction per user in the current flow, so an orphan `SpecifyUser` without a linked `Agent` may exist for that Oracle account until it is fixed or removed manually.

## Recorded outcome: first production load (2026-04-10)

The following summarizes a **live** migration report generated at **`20260410T145733Z`** (`oracle_env`: **PROD**, `dry_run`: **false**). The same structural `group_to_museum_mapping` as in dry-run exports appeared; it is omitted here because it is large and only serves as a legacy group inventory.

| Field | Value |
|--------|--------|
| `users_created` | 2361 |
| `users_skipped` | 2 |
| `agents_created` | 2360 |
| `agents_linked` | 2360 |
| `errors` | 2 |

**Interpretation.** **2360** accounts received a full **SpecifyUser + Agent** pair (`agents_linked` matches **2360**). **Two** Oracle users were **skipped** because matching Specify usernames already existed. **Two** inserts **failed** with MariaDB **“Data too long”** for **`FirstName`** and **`EMail`** respectively. Those two source rows are **test or dummy** application accounts, not part of the real user population; **not migrating them is acceptable**, and the run is treated as an **operational success** for Phase 1.4.

**Reconciling `users_created` (2361) with `agents_created` (2360).** One failure occurred **after** the login row was stored but **before** the person `Agent` row succeeded, which increments `users_created` but not `agents_created`. If the database should contain no login without an agent, remove or repair that stray `SpecifyUser` (or adjust Oracle/Specify field lengths and re-run for that account only).

## Data sensitivity

The report JSON does **not** include emails, phone numbers, or Feide identifiers. It **does** include internal **group names** and **museum codes**, and **`errors`** may include **Oracle usernames**. Store and share it like other migration operational artifacts.

## Related code

- `flows/migrate_users.py` — `MigrationResult`, `load_users_to_specify`, `_upload_report`, `migrate_users_flow`
