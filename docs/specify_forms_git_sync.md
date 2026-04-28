---
layout: default
title: Specify forms git sync
nav_order: 11
---

# Specify forms git sync

This page documents the single CLI used to round-trip Specify form XML between Specify and git:

- `scripts/form.py`

The CLI reads credentials from `.env` in the repository root (same keys as `example.env`).

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
- Writes baseline XML as `default.xml` (prefers `common` where available).
- Writes non-baseline variants under `overrides/<level>/<viewset-name>.xml`.
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

Seed a DB viewset with all forms from defaults (IaC bootstrap):

```bash
python3 scripts/form.py plan --forms-dir forms --source-mode defaults --create-missing-views
python3 scripts/form.py import --forms-dir forms --source-mode defaults --create-missing-views --backup tmp/viewset-before-seed.xml --apply
```

Behavior:

- Logs into Specify with collection context.
- Targets one viewset (auto-discovered from `/context/views.json`, or `--viewset-name`).
- Loads current remote XML from `spappresourcedata`.
- Replaces matching `<view>` and `<viewdef>` entries from local XML files.
- Can create missing `<view>` entries when `--create-missing-views` is enabled.
- PUTs updated `spappresourcedata` only when `--apply` is set and content changed.

Use `plan` with `--verbose-missing` to print unmapped files (when not using `--create-missing-views`):

```bash
python3 scripts/form.py plan --forms-dir forms --verbose-missing
```

## Suggested git workflow

1. Export full baseline once:
   - `python3 scripts/form.py export --clean --no-manifests --output-dir forms`
2. (Optional, one-time) seed DB viewset from defaults:
   - `python3 scripts/form.py import --forms-dir forms --source-mode defaults --create-missing-views --backup tmp/viewset-before-seed.xml --apply`
3. Commit all XML files (large initial commit).
4. For each admin edit cycle:
   - Re-export to `forms`
   - Review git diff
   - Commit XML changes
5. Push local XML back when needed:
   - Run `plan` first
   - Then run `import --apply`
