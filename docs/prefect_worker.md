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
4. Migration image containing runtime dependencies:
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

5. Run connectivity checks:

```bash
# TEST
prefect deployment run "Oracle Connectivity Check/oracle-connectivity-dev" --param target=TEST

# PROD
prefect deployment run "Oracle Connectivity Check/oracle-connectivity-dev" --param target=PROD
```

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

## Practical Tips

- Use explicit image tags (not only `latest`) for reproducibility.
- Keep `prefect.yaml` pull branch aligned with your active branch.
- If runs are stuck in `Scheduled`, verify worker health and in-namespace connectivity to `prefect-server:4200`.
