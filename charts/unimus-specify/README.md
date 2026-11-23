# unimus-specify

UniMus Natur **umbrella** Helm chart for staging:

- Depends on the public [specify7](https://github.com/UniMus-Natur/specify7-helm) chart (Specify app, MariaDB, Redis).
- Adds Prefect server + `prefect-dev-worker` and optional migration harness.

## Deploy

Live release name is **`specify7`** in namespace **`unimus-ns12130k`** (kube context **`nird`**). Keep that release name: Service names are `specify7-*`.

```bash
# From the meta workspace, specify7-helm must sit next to migration (file:// dependency).
helm dependency build charts/unimus-specify
helm --kube-context nird upgrade --install specify7 ./charts/unimus-specify \
  -n unimus-ns12130k \
  -f charts/unimus-specify/staging.values.yaml
```

## Values layout

| Key | Purpose |
|-----|---------|
| `specify7.*` | Passed through to the Specify7 subchart |
| `prefect.*` | Prefect server / dev worker |
| `prefect.server.persistence.*` | Optional shared RWX PVC + `subPath` for Prefect SQLite (`PREFECT_HOME`) |
| `migrationHarness.*` | Optional compare UI |
| `secrets.existingSecret` | Injected into Prefect / harness pods |

Also set `specify7.secrets.existingSecret` (and `specify7.mariadb.auth.existingSecret` when using Bitnami) to the same Secret name.

Specify keys live under `specify7:`. `prefect`, `migrationHarness`, and top-level `secrets` stay at the root of the values file.

Staging Prefect keeps SQLite on the shared RWX claim (`prefect.server.persistence`, `subPath: Prefect`), the same claim MariaDB uses under `subPath: MariaDB` and Specify under `subPath: Specify`. With persistence off, a server restart drops deployments and run history.
