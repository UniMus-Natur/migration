# unimus-specify

UniMus Natur **umbrella** Helm chart for staging:

- Depends on the public [specify7](https://github.com/UniMus-Natur/specify7-helm) chart (Specify app, MariaDB, Redis).
- Adds Prefect server + `prefect-dev-worker` and optional migration harness.

## Deploy

```bash
# From the meta workspace, specify7-helm must sit next to migration (file:// dependency).
helm dependency build charts/unimus-specify
helm upgrade --install staging ./charts/unimus-specify \
  -f charts/unimus-specify/staging.values.yaml
```

## Values layout

| Key | Purpose |
|-----|---------|
| `specify7.*` | Passed through to the Specify7 subchart |
| `prefect.*` | Prefect server / dev worker |
| `migrationHarness.*` | Optional compare UI |
| `secrets.existingSecret` | Injected into Prefect / harness pods |

Also set `specify7.secrets.existingSecret` (and `specify7.mariadb.auth.existingSecret` when using Bitnami) to the same Secret name.

## Migrating from `charts/specify7`

1. Move `charts/specify7/staging.values.yaml` → `charts/unimus-specify/staging.values.yaml`.
2. Nest former top-level Specify keys under `specify7:` (keep `prefect`, `migrationHarness`, and top-level `secrets` at the root).
3. `helm dependency build` then `helm upgrade` as above.

Service names stay `staging-specify7-*` when the release name is `staging`.
