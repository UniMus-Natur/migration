# MUSIT to Specify Migration

This repository serves as a knowledge base and workspace for migrating the Norwegian MUSIT system to Specify.

## Documentation

The full documentation is available in the `docs/` directory. 
If viewing on GitHub Pages, visit the [Documentation Site](https://unimus-natur.github.io/migration/).

## Repository Structure

- `charts/`: Kubernetes Helm charts (`unimus-specify` umbrella; generic Specify chart lives in [specify7-helm](https://github.com/UniMus-Natur/specify7-helm)).
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

Staging is deployed with the **`charts/unimus-specify`** umbrella chart (Specify7 subchart from [specify7-helm](https://github.com/UniMus-Natur/specify7-helm), plus Prefect and migration harness).

**Quick Start**:

```bash
helm dependency build charts/unimus-specify
helm upgrade --install staging ./charts/unimus-specify -f charts/unimus-specify/staging.values.yaml
```

## Versioning & Release

The project uses automatic semantic versioning. 
See [Versioning and Release Process](docs/versioning_and_release.md) for details on how releases are cut and how the Helm chart is updated.
