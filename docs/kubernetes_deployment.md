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

1.  **Specify Backend (`backend`)**: Django (Gunicorn); initContainer copies static assets to a shared PVC.
2.  **Static Server (`static`)**: Nginx serving the shared PVC.
3.  **Worker (`worker`)**: Celery.
4.  **Ingress**: `/static/*` → static; asset-server paths; `/*` → backend. Harness path is a separate Ingress from the umbrella when enabled.
5.  **MariaDB / Redis**: Bitnami subcharts of `specify7`.
6.  **Prefect**: in-cluster API + long-lived `dev-process` worker (migration image).

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

Use a gitignored overlay (move any existing `charts/specify7/staging.values.yaml` to `charts/unimus-specify/staging.values.yaml` and nest Specify keys under `specify7:`).

### 3. Deploy

```bash
helm upgrade --install staging ./charts/unimus-specify \
  -f charts/unimus-specify/staging.values.yaml
```

## Configuration

Specify settings are under `specify7.*` (see [specify7-helm values](https://github.com/UniMus-Natur/specify7-helm/blob/main/charts/specify7/values.yaml)). UniMus additions:

| Setting | Description | Default |
| :--- | :--- | :--- |
| `prefect.enabled` | Deploy Prefect server / worker | `true` |
| `prefect.devWorker.image` | Migration image for the process worker | `ghcr.io/unimus-natur/migration` |
| `migrationHarness.enabled` | Compare UI | `false` |
| `secrets.existingSecret` | Env secret for Prefect / harness (also set `specify7.secrets.existingSecret`) | `""` |

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
   helm upgrade staging ./charts/unimus-specify -f charts/unimus-specify/staging.values.yaml
   ```

Make the GHCR package visible to the cluster the same way as `migration` / `migration-harness` (`ghcr-secret` plus package visibility).
