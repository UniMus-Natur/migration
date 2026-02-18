# Versioning and Release Process

This project uses an automated semantic versioning and tagging system integrated into the build process.

## Overview

The versioning logic is handled by:
- `scripts/calculate_version.py`: Python script that determines the next version based on git history.
- `scripts/build-k8s.sh`: Bash script that orchestrates the build, tagging, and Helm chart updates.

## Branching Strategy

### Main Branch (Releases)
When code is pushed to the `main` branch, the build script:
1.  **Calculates the Next Version**:
    -   `BREAKING CHANGE` or `!: ` in commit message triggers a **MAJOR** bump.
    -   `feat:` triggers a **MINOR** bump.
    -   `fix:` (or any other commit) triggers a **PATCH** bump.
2.  **Updates Helm Charts**:
    -   Updates `version` and `appVersion` in `charts/specify7/Chart.yaml`.
    -   Updates `image.tag` in `charts/specify7/values.yaml`.
3.  **Tags and Commits**:
    -   Creates a git tag (e.g., `v1.2.3`).
    -   Commits the updated YAML files with message `chore: release v1.2.3`.
    -   Pushes the tag and commit to the repository.
4.  **Builds Image**:
    -   Triggers a Kaniko build in Kubernetes using the newly created tag as the version.

### Feature Branches (Development)
When building from any other branch:
1.  **Version**: Uses the short git hash (e.g., `a1b2c3d`).
2.  **Updates Staging Config**:
    -   Updates `image.tag` in `charts/specify7/staging.values.yaml` to the short hash.
    -   *Note*: `charts/specify7/values.yaml` is NOT touched to keep the main release version stable.
3.  **Builds Image**:
    -   Triggers a Kaniko build in Kubernetes using the short hash as the tag.

## How to Release
Simply merge your changes to `main`. The build script (run via CI/CD or manually if configured) will handle the rest.

To manually trigger the build process:
```bash
./scripts/build-k8s.sh
```

## Versioning Script Usage
You can run the version calculation directly to see what the next version would be:

```bash
python3 scripts/calculate_version.py
```

To use it to update files (advanced usage):
```bash
python3 scripts/calculate_version.py --update <version> <chart_file> <values_file> [staging_values_file]
```
