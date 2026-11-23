---
layout: default
title: Infrastructure
nav_order: 3
---

# Infrastructure


This document details the source, staging, and production environments involved in the migration.

## MUSIT Source Infrastructure (Oracle)

### Database Environments

#### Production
*   **Hostname:** `dbora-musit-prod03.uio.no`
*   **IP Address:** `129.240.118.168`
*   **Port:** `1553` (Custom Oracle port)
*   **Service Name:** Environment-driven (`ORACLE_PROD_SERVICE`) and must match the listener registration.

#### Test / Development
*   **Hostname:** `dbora-musit-utv03.uio.no`
*   **IP Address:** `129.240.118.167`
*   **Service Name (SID):** `MUSTST`
*   **Port:** `1553`

### Connection Pattern
Runtime connectivity checks in this repository use:

```text
<host>:<port>/<service_name>
```

where values are read from:

- `ORACLE_TEST_HOST`, `ORACLE_TEST_PORT`, `ORACLE_TEST_SERVICE`
- `ORACLE_PROD_HOST`, `ORACLE_PROD_PORT`, `ORACLE_PROD_SERVICE`

### Network & Access
*   **Subnet:** The MUSIT database cluster resides on the **129.240.118.x** subnet.
*   **Access Control:**
    *   Direct access to this subnet is restricted.
    *   Machine-specific firewall whitelisting or a privileged admin VPN profile is required for connectivity.
    *   DNS Resolution is available internally on the UiO network.

#### Connectivity Verification (Current Runbook Signals)
Recent in-cluster Prefect connectivity checks show:

- **TEST:** connection attempts can fail with `Connection refused` (`DPY-6005`), indicating network path/listener availability issues.
- **PROD:** host/port are reachable, but connection behavior depends on Oracle listener service configuration and server-side policies.
- **Encryption Policy:** production-side native network encryption/integrity requirements require `python-oracledb` thick mode in the runtime image.

These checks are runtime-dependent and should be revalidated through the current Prefect flow logs.

## NIRD staging

Staging runs on the NIRD Service Platform (kube context `nird`, namespace `unimus-ns12130k`, Helm release `specify7`).

*   **Specify 7**: `https://specify-stg.natur.unimus.no/`
*   **MariaDB / Redis / asset server / report runner**: same Helm release. See [Kubernetes Deployment](kubernetes_deployment.md).
*   **Prefect**: `specify7-prefect-server` and long-lived `prefect-dev-worker` (work pool `dev-process`).
*   **Test database**: `https://specify-test.natur.unimus.no/` is outside this chart. Staging MariaDB is copied there by `sync-specify-db-to-test-dev`. See [Prefect runbook](prefect_worker.md).

### Network Configuration (Firewall Whitelisting)

To allow migration flows running on Sigma2 to connect to the MUSIT Oracle database, the following Sigma2 IP ranges must be whitelisted in the USIT/MUSIT firewall.

**Source: [Sigma2 License and Access Policies](https://documentation.sigma2.no/software/licenses.html)**

#### SAGA Cluster
*   **IPv4**:
    *   `158.36.42.32/28`
    *   `158.36.42.48/28`
*   **IPv6**:
    *   `2001:700:4a01:10::/64`
    *   `2001:700:4a01:21::/64`

#### BETZY Cluster
*   **IPv4**:
    *   `158.36.154.0/28`
    *   `158.36.154.16/28`
*   **IPv6**:
    *   `2001:700:4a01:23::/64`
    *   `2001:700:4a01:24::/64`

#### NIRD Service Platform (NIRD-SP)
*   **Confirmed Range**: `158.36.102.139` - `158.36.102.150`
*   **Confirmed By**: Sigma2 Support

**IP List for Firewall Whitelisting:**
```text
158.36.102.139
158.36.102.140
158.36.102.141
158.36.102.142
158.36.102.143
158.36.102.144
158.36.102.145
158.36.102.146
158.36.102.147
158.36.102.148
158.36.102.149
158.36.102.150
```

> **Note**: This is a dynamic pool of 12 IP addresses. All addresses above must be whitelisted to ensure the migration runner can always connect to the MUSIT Oracle database.
