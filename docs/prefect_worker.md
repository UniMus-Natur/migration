# Prefect Worker for Migrations

This document describes the setup and usage of the "Inside-Out" migration workflow using long-lived Prefect Workers running within the Kubernetes cluster.

## Architecture

The Prefect Workers poll the Prefect Orchestrator (Cloud or Server) for flow runs scheduled in specific Work Pools.

- The **kubernetes worker** executes runs by creating a temporary Kubernetes Job (reproducible and production-like).
- The **dev process worker** executes runs directly inside a long-lived pod (fast feedback for development).

**Benefits:**
- **No Inbound Access Needed:** The Worker polls outbound.
- **RBAC Controlled:** Runs with specific, limited permissions.
- **Dynamic Code:** Pulls code at runtime.

## Prerequisites

1.  **Prefect Cloud Account** (or self-hosted Prefect Server).
2.  **API Key**: A Prefect API Key with permissions to join a Work Pool.
3.  **Work Pools**:
    - `kubernetes-worker` of type `kubernetes`
    - `dev-process` of type `process`

### Work Pool Configuration Tip

> [!TIP]
> **Automatic Job Cleanup**: To prevent completed pods/jobs from cluttering the namespace, configure the **Base Job Template** in your Prefect Work Pool settings to include `ttlSecondsAfterFinished`.
>
> Example addition to the Job template:
> ```yaml
> spec:
>   ttlSecondsAfterFinished: 60
> ```
> This ensures Kubernetes automatically deletes the Job 60 seconds after completion.

## Configuration

In `charts/specify7/values.yaml`, find the `prefect` section:

```yaml
prefect:
  enabled: true
  image:
    repository: prefecthq/prefect
    tag: "3.6.20-python3.12"
  
  server:
    enabled: true
    
  worker:
    enabled: true
    workPool: "kubernetes-worker"
  
  devWorker:
    enabled: true
    workPool: "dev-process"
    image:
      repository: ""  # Defaults to migration.image.repository
      tag: ""         # Defaults to migration.image.tag
```

If `server.enabled` is true, both workers will automatically connect to the internal Prefect server. If you are using a self-hosted external server or Prefect Cloud, set `server.enabled: false` and provide `PREFECT_API_KEY` and `PREFECT_API_URL` via the `secrets.existingSecret` property, referencing a Kubernetes Secret created from your `.env` file.

## Deployment

Deploy using Helm, ensuring you provide the `secrets.existingSecret` value:

```bash
kubectl create secret generic specify7-env --from-env-file=example.env

helm upgrade --install specify7 ./charts/specify7 \
  --set prefect.enabled=true \
  --set secrets.existingSecret=specify7-env
```

## Developer Experience

Use two pools for a fast-but-safe workflow:

1.  **Rapid Development (`dev-process`)**
    - Sync code into the dev worker pod (for example with `kubectl cp`, DevSpace, or Mutagen).
    - Trigger runs from deployments targeting the `dev-process` pool.
    - Runs execute immediately in the worker pod without rebuilding images.
2.  **Validation / Production-like (`kubernetes-worker`)**
    - Build and push a tagged image.
    - Trigger runs from deployments targeting the `kubernetes-worker` pool.
    - Worker creates isolated Kubernetes Jobs from the image.

## Troubleshooting

-   **Worker Logs**: Check both workers to ensure they are connected:
    ```bash
    kubectl logs -l component=prefect-worker
    kubectl logs -l component=prefect-dev-worker
    ```
-   **Permissions**: If the created Jobs fail to start, check RBAC errors in events:
    ```bash
    kubectl get events --sort-by=.metadata.creationTimestamp
    ```
