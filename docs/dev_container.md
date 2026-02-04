---
layout: default
title: Dev / Migration Container
nav_order: 6
---

# Dev / Migration Container

This guide details how to build and use the "Migration Runner" container. This container runs inside the Kubernetes cluster and provides a persistent environment with access to:
1.  **Specify 7 Source Code** (with ORM).
2.  **Internal Cluster Network** (MariaDB, Redis).
3.  **External Oracle Database** (via VPN/Firewall whitelisting).

## 1. Build the Image (Podman)

Since the cluster may be running on a different architecture or we want to ensure compatibility, we build for `linux/amd64`.

**Prerequisites**:
- `podman` installed.
- Submodules initialized (`git submodule update --init --recursive`).

**Build Command**:
```bash
podman build --platform linux/amd64 -t ghcr.io/unimus-natur/migration:latest .
```

*Note: If you are pushing to a registry (e.g., GHCR), tag accordingly:*
```bash
podman tag ghcr.io/unimus-natur/migration:latest ghcr.io/unimus-natur/migration:latest
podman push ghcr.io/unimus-natur/migration:latest
```

## 2. Deploy to Cluster

The container is deployed as part of the `specify7` Helm chart.

1.  **Load Image (Local Dev)**:
    If using `kind` or `minikube` and not pushing to a registry, load the image:
    ```bash
    # For kind
    kind load docker-image migration:latest
    # (Podman users might need to save/load archive if direct load isn't supported)
    podman save migration:latest -o migration.tar
    kind load image-archive migration.tar
    ```

2.  **Enable in Helm**:
    Ensure `migration.enabled: true` is set in `values.yaml` (default).

3.  **Upgrade/Install**:
    ```bash
    helm upgrade --install staging ./charts/specify7 --values ./charts/specify7/values.yaml
    ```

## 3. Accessing the Container

Find the pod name and execute a shell:

```bash
# Get Pod Name
export POD_NAME=$(kubectl get pods -l component=migration -o jsonpath="{.items[0].metadata.name}")

# Enter Container
kubectl exec -it $POD_NAME -- bash
```

## 4. Database Proxies (Port Forwarding)

To access databases (Oracle or Cluster MariaDB) from your **local machine** using tools like DBeaver or DbGate, use the included helper script.

### Inside the Container:
Start the proxies. This binds `socat` to the pod's ports.

```bash
# Forward Oracle (Prod: 1553, Test: 1553)
./scripts/proxy_db.sh oracle

# Forward Cluster MariaDB (3306)
./scripts/proxy_db.sh mariadb
```

### From Local Machine:
Forward the pod's ports to your localhost.

```bash
kubectl port-forward $POD_NAME 1553:1553 3306:3306
```

### Connect:
*   **Oracle**: `localhost:1553`
*   **MariaDB**: `localhost:3306`

## 5. Running Migration Scripts

The container has the full repo at `/app` and the `specify7` submodule at `/app/specify7`.

To run scripts using the Specify ORM:

```bash
# Example
python scripts/test_setup.py
```

## 6. Remote Build (Kaniko on K8s)

You can trigger a remote build (Kaniko on K8s) directly from your terminal using the helper script.

### 1. Prerequisites (One-time Setup)

**Create Secret**:
The cluster needs your GitHub Container Registry credentials.
```bash
# Replace YOUR_TOKEN with a GitHub Classic PAT (read:packages, write:packages)
kubectl create secret docker-registry ghcr-secret \
  --docker-server=ghcr.io \
  --docker-username=unimus-natur \
  --docker-password=YOUR_TOKEN
```

### 2. Usage

**Build Current Branch**:
Builds the current remote state of your branch and pushes to `ghcr.io/unimus-natur/migration:latest`.
```bash
./scripts/build-k8s.sh
```

**Build Specific Branch**:
```bash
./scripts/build-k8s.sh feature/new-setup
```

**Build Custom Branch & Tag**:
```bash
./scripts/build-k8s.sh feature/new-setup ghcr.io/unimus-natur/migration:test-1
```
