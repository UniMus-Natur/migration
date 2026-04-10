# Prefect Runbook (K8s Dev Worker)

This runbook describes the current Prefect workflow for rapid in-cluster development.

## Current Model

- `prefect-server` runs inside the namespace and exposes API/UI on port `4200`.
- `prefect-dev-worker` runs as a long-lived worker pod using work pool `dev-process` (`process` type).
- Deployments in `prefect.yaml` run `git_clone` on each flow run, so code is pulled from Git (branch `add-prefect`) instead of baked `/app` source.

This means you do **not** need to rebuild the image for normal code changes; commit and push is enough.

## Required Components

1. Helm release with Prefect enabled and secret injection configured.
2. A `dev-process` work pool in Prefect.
3. Valid Oracle credentials in `secrets.existingSecret` (e.g. `specify-secret`).
4. **S3 report uploads:** Flows such as **Migrate Users** and **Migrate MUSIT Actors** only write `report.json` to the bucket when **`S3_BUCKET`** is set in that same secret (plus S3/MinIO credentials and optional `S3_PREFIX`). If `S3_BUCKET` is absent, runs succeed but **`report_uploaded`** in the flow result is **`false`** — see [Migration reports on S3](migration_s3_reports.md#troubleshooting-nothing-appeared-in-the-bucket).
5. Migration image containing runtime dependencies:
   - `prefect`
   - `python-oracledb` with Oracle Instant Client for thick mode

## Helm Configuration Notes

In `charts/specify7/staging.values.yaml`:

- `prefect.server.enabled: true`
- `prefect.devWorker.enabled: true`
- `prefect.devWorker.workPool: "dev-process"`
- `prefect.devWorker.image.*` points to your migration image tag.
- `secrets.existingSecret` points to the env secret with Oracle and Prefect vars.

## Daily Dev Loop

1. Start API access:

```bash
kubectl port-forward svc/specify7-prefect-server 4200:4200
```

2. In another terminal:

```bash
source .venv/bin/activate
export PREFECT_API_URL=http://127.0.0.1:4200/api
```

3. Commit and push code changes to `add-prefect` (the branch configured in `prefect.yaml`).

4. Register/update deployment:

```bash
prefect deploy --all
```

**Non-interactive deploys:** This repo includes a root [`prefect.toml`](../prefect.toml) with `[cli] prompt = false`, so Prefect loads **`PREFECT_CLI_PROMPT=false`** when your shell’s current working directory is the project root (see `prefect config view`). You should not get per-deployment confirmation prompts. If you run Prefect from elsewhere, either `cd` into the repo first, use an explicit flag, or set the variable for that shell:

```bash
prefect deploy --no-prompt --all
# or
PREFECT_CLI_PROMPT=false prefect deploy --all
```

To persist the setting in a **Prefect profile** instead of `prefect.toml`, use `prefect config set PREFECT_CLI_PROMPT=false` (writes to the active profile; see [Settings and profiles](https://docs.prefect.io/v3/concepts/settings-and-profiles)).

5. Run PROD connectivity check (Oracle + S3 preflight):

```bash
prefect deployment run "Oracle Connectivity Prod Check/oracle-connectivity-prod-dev"
```

Optional: run Oracle schema snapshot export (uploads JSON/CSV to S3):

```bash
prefect deployment run "Oracle Schema Snapshot/oracle-schema-snapshot-dev"
```

Schema snapshot artifacts include `schema_catalog.json`, CSV extracts, and `schema.dbml`.

Optional: migrate MUSIT **`ACTOR`** + **`PERSON_NAME`** into Specify **`Agent`** (Phase 1.1; default is dry run):

```bash
prefect deployment run "Migrate MUSIT Actors/migrate-musit-agents-dev"
```

See [MUSIT collection agents migration](migrate_musit_agents.md) for parameters and scope.

6. Inspect results:

```bash
prefect flow-run ls
prefect flow-run logs <FLOW_RUN_ID>
kubectl logs -f -l component=prefect-dev-worker
```

## Known Oracle Failure Patterns

- `DPY-6005 ... [Errno 111] Connection refused`  
  Network path or listener is not reachable from the cluster.

- `DPY-6001 ... service is not registered (ORA-12514-like)`  
  Host/port are reachable, but `ORACLE_*_SERVICE` is wrong for that listener.

- `DPY-3001 ... only supported in thick mode`  
  Server requires native network encryption/integrity; thick mode is required.

- `DPI-1047 ... cannot locate libclntsh.so`  
  Oracle Instant Client library is missing/invisible in image or stale image tag is still running.

- `S3 upload errors`  
  Verify `S3_BUCKET`, credentials, endpoint/region, and path-style settings in your secret.
  For MinIO/proxy setups with `XAmzContentSHA256Mismatch`, set `S3_PAYLOAD_SIGNING_ENABLED=false`.

## Practical Tips

- Use explicit image tags (not only `latest`) for reproducibility.
- Keep `prefect.yaml` pull branch aligned with your active branch.
- If runs are stuck in `Scheduled`, verify worker health and in-namespace connectivity to `prefect-server:4200`.
