---
layout: default
title: Specify forms git sync
nav_order: 11
---

# Specify forms git sync

This page documents the current scripts used to round-trip Specify form XML between Specify and git:

- `scripts/export_specify_forms.py`
- `scripts/import_specify_forms.py`

Both scripts read credentials from `.env` in the repository root (same keys as `example.env`).

## Required env vars

- `SPECIFY7_URL`
- `SPECIFY7_USER`
- `SPECIFY7_PASSWORD`
- `SPECIFY7_COLLECTION` (optional; defaults to first available collection if unset)

## Export forms from Specify to git

Full export (recommended baseline for git history):

```bash
python3 scripts/export_specify_forms.py --clean --output-dir forms
```

XML-focused export (skip per-form manifests):

```bash
python3 scripts/export_specify_forms.py --clean --no-manifests --output-dir forms
```

Behavior:

- Scans Specify views via `/context/views.json`.
- Writes one directory per `table/view_name`.
- Writes form XML as `default.xml` in each form directory.
- Always writes top-level `summary.json`.

## Import forms from git to Specify

`import_specify_forms.py` is **dry-run by default**.

Dry-run:

```bash
python3 scripts/import_specify_forms.py --forms-dir forms
```

Apply changes:

```bash
python3 scripts/import_specify_forms.py --forms-dir forms --apply
```

Apply with backup of current remote viewset XML:

```bash
python3 scripts/import_specify_forms.py \
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

Use `--verbose-missing` to print each skipped file:

```bash
python3 scripts/import_specify_forms.py --forms-dir forms --verbose-missing
```

## Suggested git workflow

1. Export full baseline once:
   - `python3 scripts/export_specify_forms.py --clean --no-manifests --output-dir forms`
2. Commit all XML files (large initial commit).
3. For each admin edit cycle:
   - Re-export to `forms`
   - Review git diff
   - Commit XML changes
4. Push local XML back when needed:
   - Dry-run import first
   - Then run with `--apply`
