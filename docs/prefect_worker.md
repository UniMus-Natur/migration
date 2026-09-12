# Prefect Runbook (K8s Dev Worker)

This runbook describes the current Prefect workflow for rapid in-cluster development.

## Current Model

- `prefect-server` runs inside the namespace and exposes API/UI on port `4200`.
- `prefect-dev-worker` runs as a long-lived worker pod using work pool `dev-process` (`process` type).
- Deployments in `prefect.yaml` run `git_clone` on each flow run, so code is pulled from Git (branch `dev`) instead of baked `/app` source.

This means you do **not** need to rebuild the image for normal code changes; commit and push is enough.

## Required Components

1. Helm release with Prefect enabled and secret injection configured.
2. A `dev-process` work pool in Prefect.
3. Valid Oracle credentials in `secrets.existingSecret` (e.g. `specify-secret`).
4. **S3 report uploads:** Flows such as **Migrate Users** and **Migrate MUSIT Actors** only write `report.json` when **`S3_BUCKET`** is set (plus S3/MinIO credentials). Reports use **`S3_MIGRATION_REPORTS_PREFIX`** (default `migration-reports`), not **`S3_PREFIX`** / `oracle-schema`. If `S3_BUCKET` is absent, runs succeed but **`report_uploaded`** is **`false`** — see [Migration reports on S3](migration_s3_reports.md#troubleshooting-nothing-appeared-in-the-bucket).
5. Migration image containing runtime dependencies:
   - `prefect`
   - `python-oracledb` with Oracle Instant Client for thick mode

## Helm Configuration Notes

In `charts/specify7/staging.values.yaml`:

- `prefect.server.enabled: true`
- `prefect.devWorker.enabled: true`
- `prefect.devWorker.workPool: "dev-process"`
- `prefect.devWorker.image.*` points to your migration image tag.
- `prefect.devWorker.resources.limits.memory`: long specimen migrations (Django + Oracle + media uploads) need more than **1Gi**. Staging uses **4Gi** (namespace quota is **32G** with ~25Gi already allocated; **8Gi** cannot schedule). Limit/request ratio must be ≤ **2:1**.
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

3. Commit and push code changes to `dev` (the branch configured in `prefect.yaml`).

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
prefect deployment run "Infrastructure Prod Check/infrastructure-prod-check-dev"
```

Optional: run Oracle schema snapshot export (uploads JSON/CSV to S3):

```bash
prefect deployment run "Oracle Schema Snapshot/oracle-schema-snapshot-dev"
```

Schema snapshot artifacts include `schema_catalog.json`, CSV extracts, and `schema.dbml`.

Optional: sync Specify hierarchy from YAML (post-bootstrap, idempotent; default is dry run):

```bash
prefect deployment run "Sync Specify structure/sync-specify-structure-dev" --param dry_run=false
```

See [Specify structure sync](sync_specify_structure.md) for the YAML format and recorded outcomes.

Optional: migrate MUSIT **`ACTOR`** + **`PERSON_NAME`** into Specify **`Agent`** (Phase 1.1; default is dry run):

```bash
prefect deployment run "Migrate MUSIT Actors/migrate-musit-agents-dev"
```

See [MUSIT collection agents migration](migrate_musit_agents.md) for parameters and scope.

Optional: fill **`AgentVariant`** from alternate MUSIT **`PERSON_NAME`** rows (Phase 1.1b; requires agents already migrated; default is dry run):

```bash
prefect deployment run "Migrate MUSIT Agent Variants/migrate-musit-agent-variants-dev"
```

See [MUSIT agent name variants](migrate_musit_agent_variants.md).

Optional: fill remaining MUSIT **`ACTOR`** person-module fields (URL note / Wikidata, address, dates from note tags, …) onto existing Agents (Phase 1.1c; default is dry run):

```bash
prefect deployment run "Migrate MUSIT Agent Details/migrate-musit-agent-details-dev"
```

See [MUSIT agent details fill-in](migrate_musit_agent_details.md).

Optional: migrate application users from Oracle `USD_METADATA` into Specify **`SpecifyUser`** + **`Agent`** (Phase 1.4; default is dry run):

```bash
prefect deployment run "Migrate Users/migrate-users-dev" --param dry_run=false
```

See [User migration report](user_migration_report.md) for the report format and recorded outcomes.

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

- `git_clone` failed with `could not read Username for 'https://github.com'` / exit 128  
  This is **not** a migration code bug and usually **not** a GitHub outage. The worker clones `https://github.com/UniMus-Natur/migration.git` (branch `dev`, with `specify7` submodule) on every flow run. Anonymous HTTPS from cluster egress IPs often hits GitHub rate limits; Git then tries to prompt for credentials and fails in a non-interactive pod (`No such device or address`).  
  **Fix:** ensure `GITHUB_TOKEN` (read-only PAT, `public_repo` scope) is set in `specify-secret` on the prefect-dev-worker. `prefect.yaml` rewrites `https://github.com/` URLs to use it before `git_clone` (including the `specify7` submodule). Then `prefect deploy --all` and retry.

- `Process exited with status code -9` / `SIGKILL` / memory allocation message  
  The dev worker pod hit its cgroup memory limit (check `kubectl describe pod -l component=prefect-dev-worker` → Limits). Raise `prefect.devWorker.resources.limits.memory` in Helm and restart the deployment.

## Sync staging DB → test (SSH tunnel)

Flow: **Sync Specify DB to Test** / `sync-specify-db-to-test-dev`.

Streams a full logical dump of the in-cluster staging MariaDB into the test database through an SSH LocalForward on the prefect-dev-worker. **Hard-fails** unless SHA-256 fingerprints of `information_schema.COLUMNS` for the app schema match, unless you pass `force=true` (intentional wipe-and-replace when the target was bootstrapped with different DDL). After a live restore, fingerprints must still match. `spversion` is logged for diagnostics but not required on the target (empty cloud DBs are OK).

Dump omits `CREATE DATABASE` so source/target schema names may differ (e.g. staging `specify` → cloud `norway`); the restore client selects `TEST_DB_NAME`. The test DB user needs `ALL` on that schema only (not global `CREATE DATABASE`).

Does **not** copy S3 attachments, Redis, or Oracle. Default `dry_run=true`, `force=false`.

### Setup

1. Rebuild the migration image so it includes `openssh-client` (see root `Dockerfile`), roll the prefect-dev-worker.
2. Create a key secret and enable the Helm mount:

```bash
kubectl create secret generic specify-test-db-ssh --from-file=id_ed25519=./id_ed25519
```

In Helm values (`prefect.devWorker`):

```yaml
sshKeySecret: "specify-test-db-ssh"
sshKeySecretKey: "id_ed25519"
sshKeyMountPath: "/var/secrets/test-db-ssh"
```

3. Put bastion + test DB settings in `specify-secret` (see `example.env` section *Sync staging Specify DB → test*). The chart sets `TEST_DB_SSH_PRIVATE_KEY_PATH` when `sshKeySecret` is set.
4. Test DB user needs privileges to replace objects in `TEST_DB_NAME` (`DROP`/`CREATE` table, routines, etc.).

### Run

```bash
prefect deployment run "Sync Specify DB to Test/sync-specify-db-to-test-dev" -p dry_run=true
# first sync when test DDL differs from staging (empty Django bootstrap, etc.):
prefect deployment run "Sync Specify DB to Test/sync-specify-db-to-test-dev" \
  -p dry_run=false -p force=true
# later syncs once fingerprints already match:
prefect deployment run "Sync Specify DB to Test/sync-specify-db-to-test-dev" -p dry_run=false
```

## Practical Tips

- Use explicit image tags (not only `latest`) for reproducibility.
- Keep `prefect.yaml` pull branch aligned with your active branch.
- If runs are stuck in `Scheduled`, verify worker health and in-namespace connectivity to `prefect-server:4200`.
