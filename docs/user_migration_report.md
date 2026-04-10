---
layout: default
title: User migration report
nav_order: 7
---

# User migration report (`migration_report.json`)

This document describes the JSON artifact written by the Prefect flow **Migrate Users** (`flows/migrate_users.py`, `migrate_users_flow`). That flow implements **Phase 1.4** in the phased strategy: loading application users from Oracle `USD_METADATA` into Specify 7 as `SpecifyUser` and `Agent` records. For the overall plan, see [Migration Strategy (Phased)](migration_strategy.md).

## Purpose

The report captures **run metadata**, **aggregate counts**, **per-user error messages** (if any), and an **inventory of Oracle group names grouped by museum**. It is intended for auditing a run and for later design work mapping legacy groups to Specify collection access (the flow does not assign Specify permissions from groups today).

## How it is produced

1. **Extract** — Users, memberships, and groups are read from `USD_METADATA.BRUKARAR`, `BRUKERNAVN_GRUPPE`, and `GRUPPE`.
2. **Load** — For each Oracle user, the flow either skips (existing `SpecifyUser` with the same username), simulates creation (`dry_run=True`), or creates `SpecifyUser` and `Agent` via the Django ORM (`dry_run=False`).
3. **Report** — The flow builds a JSON object and, when `S3_BUCKET` is set, uploads it with `upload_file_with_compat_retry` to object storage. If `S3_BUCKET` is unset, nothing is uploaded; the Prefect run return value still includes the same counters and error list.

Object key pattern (timestamp is UTC at upload time):

- With prefix: `{S3_PREFIX}/user-migration/{YYYYMMDDTHHMMSSZ}/migration_report.json`
- Default `S3_PREFIX` if unset: `oracle-schema`

A copy saved under `schemas/migration_report.json` is useful locally but is **gitignored**; treat any checked-out path as a generated sample, not source of truth.

## JSON fields

| Field | Type | Description |
|--------|------|-------------|
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

## Data sensitivity

The report JSON does **not** include emails, phone numbers, or Feide identifiers. It **does** include internal **group names** and **museum codes**, and **`errors`** may include **Oracle usernames**. Store and share it like other migration operational artifacts.

## Related code

- `flows/migrate_users.py` — `MigrationResult`, `load_users_to_specify`, `_upload_report`, `migrate_users_flow`
