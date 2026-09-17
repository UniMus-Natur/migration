---
layout: default
title: Kubernetes Deployment
nav_order: 4
---

# Kubernetes Deployment

We use a custom Helm chart to deploy the staging environment on the NIRD Kubernetes cluster.

## Architecture

The deployment splits the monolithic stack into cloud-native components:

1.  **Specify Backend (`backend`)**: Runs the Django application (Gunicorn).
    -   Uses an **InitContainer** to copy static assets (`.js`, `.css`) from the official image to a Shared PVC.
    -   Mounts configuration from a Kubernetes Secret.
2.  **Static Server (`static`)**: A lightweight Nginx service.
    -   Mounts the Shared PVC to serve the assets extracted by the backend.
    -   Handles path rewrites (e.g., `/static/config/` -> generated settings).
3.  **Worker (`worker`)**: Runs the Celery worker for asynchronous tasks.
4.  **Ingress**: Routes traffic:
    -   `/static/*` -> Static Server
    -   `/*` -> Specify Backend
5.  **MariaDB**: A dedicated MariaDB instance running as a sub-chart dependency (Bitnami).

## Prerequisites

- Kubernetes Cluster (v1.19+)
- `ReadWriteMany` Storage Class (Required for shared assets)
- Ingress Controller
- Helm 3.x
- **Bitnami Repo**: `helm repo add bitnami https://charts.bitnami.com/bitnami`

## Quick Start

### 1. Build Dependencies
The chart depends on the `bitnami/mariadb` chart. You must build this dependency first.

```bash
helm dependency build charts/specify7
```

### 2. Configure Credentials
Edit `charts/specify7/values.yaml` to set your passwords and secrets.

### 3. Deploy
```bash
helm install staging ./charts/specify7 \
  --set ingress.enabled=true \
  --set ingress.hosts[0].host=specify.yourdomain.com
```

## Configuration

| Setting | Description | Default |
| :--- | :--- | :--- |
| `persistence.storageClass` | **Critical**: Must support RWX | `""` (defaultSC) |
| `mariadb.enabled` | Enable the bundled MariaDB | `true` |
| `specify.database` | Database connection (auto-configured if mariadb enabled) | `mariadb` |
| `specify.secretKey` | Django Secret Key | `change-me...` |
| `reportRunner.fontsJar` | Jasper font-extension jar for labels/reports | `enabled: false` |

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
3. Enable `reportRunner.fontsJar` (already on in `staging.values.yaml`) and upgrade:

   ```bash
   helm upgrade staging ./charts/specify7 -f charts/specify7/staging.values.yaml
   ```

Make the GHCR package visible to the cluster the same way as `migration` / `migration-harness` (`ghcr-secret` plus package visibility).
