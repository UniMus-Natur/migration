---
layout: default
title: Specify forms git sync
nav_order: 11
---

# Specify forms git sync

This page documents the single CLI used to round-trip Specify form XML between Specify and git:

- `scripts/form.py`

Both scripts read credentials from `.env` in the repository root (same keys as `example.env`).

## Required env vars

- `SPECIFY7_URL`
- `SPECIFY7_USER`
- `SPECIFY7_PASSWORD`
- `SPECIFY7_COLLECTION` (optional; defaults to first available collection if unset)

## Commands

The workflow is split into three subcommands:

- `export` - pull forms from Specify to files
- `plan` - dry-run sync plan from files to Specify
- `import` - apply files to Specify (only with `--apply`)

## Export forms from Specify to git

Full export (recommended baseline for git history):

```bash
python3 scripts/form.py export --clean --output-dir forms
```

XML-focused export (skip per-form manifests):

```bash
python3 scripts/form.py export --clean --no-manifests --output-dir forms
```

Behavior:

- Scans Specify views via `/context/views.json`.
- Writes one directory per `table/view_name`.
- Writes form XML as `default.xml` in each form directory.
- Always writes top-level `summary.json`.

## Plan and import forms from git to Specify

`plan` is always dry-run.  
`import` is dry-run unless `--apply` is provided.

Dry-run:

```bash
python3 scripts/form.py plan --forms-dir forms
```

Apply changes:

```bash
python3 scripts/form.py import --forms-dir forms --apply
```

Apply with backup of current remote viewset XML:

```bash
python3 scripts/form.py import \
  --forms-dir forms \
  --backup tmp/viewset-backup.xml \
  --apply
```

Behavior:

- Logs into Specify with collection context.
- Targets one viewset (auto-discovered from `/context/views.json`, or `--viewset-name`).
- Loads current remote XML from `spappresourcedata`.
- Replaces matching `<view>` and `<viewdef>` entries from local XML files.
- PUTs updated `spappresourcedata` only when `--apply` is set and content changed.

## Current limitation in this environment

In the current staging data, the active DB-backed viewset contains only a subset of forms (observed as ~10 mapped forms in dry run).  
As a result:

- Local XML files that do not map to a remote `<view name,class>` in the target viewset are counted as `missing_forms`.
- Those missing forms are skipped by import.

Use `plan` with `--verbose-missing` to print each skipped file:

```bash
python3 scripts/form.py plan --forms-dir forms --verbose-missing
```

## Suggested git workflow

1. Export full baseline once:
   - `python3 scripts/form.py export --clean --no-manifests --output-dir forms`
2. Commit all XML files (large initial commit).
3. For each admin edit cycle:
   - Re-export to `forms`
   - Review git diff
   - Commit XML changes
4. Push local XML back when needed:
   - Run `plan` first
   - Then run `import --apply`
