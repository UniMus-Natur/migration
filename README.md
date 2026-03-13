# MUSIT to Specify Migration

This repository serves as a knowledge base and workspace for migrating the Norwegian MUSIT system to Specify.

## Documentation

The full documentation is available in the `docs/` directory. 
If viewing on GitHub Pages, visit the [Documentation Site](https://unimus-natur.github.io/migration/).

## Repository Structure

- `charts/`: Kubernetes Helm charts (including `specify7`).
- `config/`: Configuration files (local settings).
- `docs/`: Knowledge base source files (Markdown).
- `flows/`: Prefect flows for migration logic.
- `specify7/`: [Submodule] Official Specify 7 repository.

## Development Setup

1.  **Install Requirements**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    -   Copy `config/local_specify_settings.py` (if missing, see the template using the `bootstrap.py` logic) and configure your database credentials.
    -   *Note*: `config/local_specify_settings.py` is git-ignored.

3.  **Running Flows**:
    Flows in `flows/` utilize a `bootstrap.py` helper to load the Specify 7 environment dynamically.
    ```bash
    python flows/test_infrastructure.py
    ```

## Dev / Migration Container

For advanced debugging, database proxying, and running migration flows inside the cluster, see the [Dev Container Guide](docs/dev_container.md).
For the current Prefect in-cluster workflow, see the [Prefect Runbook](docs/prefect_worker.md).

It supports:
- Building with **Podman** (x86/amd64).
- **Proxying** Oracle and MariaDB to your local machine.
- Running flows with full **ORM access** in the cluster.

## Kubernetes Deployment

A custom Helm chart is provided in `charts/specify7` to deploy a staging environment.

**Features**:
-   Official Specify 7 images (no custom builds).
-   Split architecture: Backend (Gunicorn) + Static Server (Nginx).
-   Shared Assets via `ReadWriteMany` PVC.
-   Integrated MariaDB (Bitnami) for self-contained staging.

**Quick Start**:

```bash
helm install staging ./charts/specify7
```

## Versioning & Release

The project uses automatic semantic versioning. 
See [Versioning and Release Process](docs/versioning_and_release.md) for details on how releases are cut and how the Helm chart is updated.
