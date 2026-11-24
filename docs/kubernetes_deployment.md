---
layout: default
title: Kubernetes Deployment
nav_order: 4
---

# Kubernetes Deployment

Staging is deployed with the **`charts/unimus-specify`** umbrella chart:

- **Specify7 stack** comes from the public [specify7-helm](https://github.com/UniMus-Natur/specify7-helm) chart (subchart dependency).
- **Prefect** (server + `prefect-dev-worker`) and **migration harness** are UniMus templates in this repo.

Specify values nest under `specify7:` in the umbrella values / `staging.values.yaml`.

## Architecture

1.  **Specify Backend (`backend`)**: Django (Gunicorn); initContainer copies static assets to a shared PVC (`subPath: Specify`).
2.  **Static Server (`static`)**: Nginx serving that same PVC.
3.  **Celery worker**: off in staging (`specify7.worker.enabled: false`). Jobs run on the Prefect dev worker.
4.  **Ingress**: `/static/*` → static; asset-server paths; `/*` → backend. Harness path is a separate Ingress from the umbrella when enabled.
5.  **MariaDB / Redis**: Bitnami subcharts of `specify7`. MariaDB data is `subPath: MariaDB` on the shared RWX claim.
6.  **Prefect**: in-cluster API + long-lived `dev-process` worker (migration image). Server SQLite is `subPath: Prefect` on the same claim when `prefect.server.persistence.enabled` is true.

## Prerequisites

- Kubernetes Cluster (v1.19+)
- `ReadWriteMany` Storage Class (Required for shared assets)
- Ingress Controller
- Helm 3.x
- **Bitnami Repo**: `helm repo add bitnami https://charts.bitnami.com/bitnami`
- Local clone of `specify7-helm` next to `migration` (meta workspace / `file://` dependency), or switch the umbrella `Chart.yaml` dependency to the published OCI chart.

## Quick Start

### 1. Build Dependencies

```bash
helm dependency build charts/unimus-specify
```

### 2. Configure Credentials

Use the gitignored overlay `charts/unimus-specify/staging.values.yaml`. Specify keys nest under `specify7:`.

### 3. Deploy

Release name is **`specify7`**, namespace **`unimus-ns12130k`**, context **`nird`**.

```bash
helm --kube-context nird upgrade --install specify7 ./charts/unimus-specify \
  -n unimus-ns12130k \
  -f charts/unimus-specify/staging.values.yaml
```

## Configuration

Specify settings are under `specify7.*` (see [specify7-helm values](https://github.com/UniMus-Natur/specify7-helm/blob/main/charts/specify7/values.yaml)). UniMus additions:

| Setting | Description | Default |
| :--- | :--- | :--- |
| `prefect.enabled` | Deploy Prefect server / worker | `true` |
| `prefect.server.persistence` | Shared RWX PVC + `subPath: Prefect` for Prefect SQLite (`PREFECT_HOME`) | off unless `existingClaim` is set |
| `prefect.devWorker.image` | Migration image for the process worker | `ghcr.io/unimus-natur/migration` |
| `migrationHarness.enabled` | Compare UI | `false` |
| `secrets.existingSecret` | Env secret for Prefect / harness (also set `specify7.secrets.existingSecret`) | `""` |

Staging turns persistence on and points `existingClaim` at the same RWX volume as MariaDB and Specify. A server pod restart then keeps deployments, the `dev-process` work pool, and run history.

## Specify7 test image

Staging normally runs the official image (`specifyconsortium/specify7-service`, tag set in `staging.values.yaml`). To test the `specify7/` submodule, build that Dockerfile’s `run` target and push it to GHCR. The registry is the same one the Prefect dev worker already uses: `ghcr.io/unimus-natur` (worker image `ghcr.io/unimus-natur/migration`). The app image is `ghcr.io/unimus-natur/specify7-service:<submodule sha>`. `specify7.imagePullSecrets` already includes `ghcr-secret`.

Commit in the `specify7/` submodule, then run the script. It pushes that commit to this repo as `refs/tags/specify7-<sha>` (the tag’s tree is the Specify 7 source, not the migration project) and Kaniko clones that tag. The submodule remote stays `https://github.com/specify/specify7`.

```bash
./scripts/build-specify7-k8s.sh
```

A successful build rewrites `specify7.image` in `charts/unimus-specify/staging.values.yaml` (`pullPolicy: Always`). Deploy with:

```bash
helm --kube-context nird upgrade specify7 ./charts/unimus-specify \
  -n unimus-ns12130k \
  -f charts/unimus-specify/staging.values.yaml
```

To go back to the official image, set `specify7.image.repository` to `specifyconsortium/specify7-service` and `specify7.image.tag` to the release tag (currently `v7.12.2`), then run the same upgrade.

## Report fonts

The [report-runner service](https://github.com/specify/report-runner-service#fonts) uses built-in PDF fonts unless a Jasper font-extension jar is mounted at:

```
/var/lib/jetty/webapps/ROOT/WEB-INF/lib/report-fonts.jar
```

That is the Docker volume equivalent of:

```
-v ./report-fonts.jar:/var/lib/jetty/webapps/ROOT/WEB-INF/lib/report-fonts.jar
```

The jar in `fonts/report-fonts.jar` is ~7.6MiB, so it **cannot** be stored in a Kubernetes ConfigMap (1MiB etcd limit). The chart copies it from a tiny image (`fonts/Dockerfile`) into an `emptyDir`, then bind-mounts the file into Jetty.

1. Commit and push `fonts/report-fonts.jar` (Kaniko builds from the remote branch).
2. Build and push the image:

   ```bash
   ./scripts/build-report-fonts-k8s.sh
   ```

   Staging values expect `ghcr.io/unimus-natur/report-fonts:latest`.
3. Enable `specify7.reportRunner.fontsJar` (already on in `staging.values.yaml`) and upgrade:

   ```bash
   helm --kube-context nird upgrade specify7 ./charts/unimus-specify \
     -n unimus-ns12130k \
     -f charts/unimus-specify/staging.values.yaml
   ```

Make the GHCR package visible to the cluster the same way as `migration` / `migration-harness` (`ghcr-secret` plus package visibility).
