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
*   **Service Name:** *(Standard Production SID usually follows `MUSIT` or similar naming)*

#### Test / Development
*   **Hostname:** `dbora-musit-utv03.uio.no`
*   **IP Address:** `129.240.118.167`
*   **Service Name (SID):** `MUSTST`
*   **Port:** `1553` *(Likely shares configuration with production)* or `1521`

### Connection Strings
Based on the identified infrastructure, the standard Oracle JDBC connection string formats are:

**Test Environment:**
```text
jdbc:oracle:thin:@//dbora-musit-utv03.uio.no:1553/MUSTST
```

### Network & Access
*   **Subnet:** The MUSIT database cluster resides on the **129.240.118.x** subnet.
*   **Access Control:**
    *   Direct access to this subnet is restricted.
    *   Machine-specific firewall whitelisting or a privileged admin VPN profile is required for connectivity.
    *   DNS Resolution is available internally on the UiO network.

#### Connectivity Verification (Firewall Status)
**Confirmed: Blocked**

Both Test and Production environments are unreachable on their designated ports from the current location (VPN).

| Environment | Host | Port | Result | Implication |
| :--- | :--- | :--- | :--- | :--- |
| **Test** | `dbora-musit-utv03.uio.no` | 1553, 1521 | **Timeout** | Firewall blocked. |
| **Prod** | `dbora-musit-prod03.uio.no` | 1553 | **Timeout** | Firewall blocked. |

*Note: Ping (ICMP) works for both hosts, proving the servers are online, but TCP ports are filtered.*

## Sigma2 Resources (Staging)

We plan to use Sigma2 for the staging environment and computation.

### Application Details
*   **Architecture**: Kubernetes cluster hosting Dockerized microservices. (See [**Kubernetes Deployment**](kubernetes_deployment.md) for details).
*   **Components**: 
    *   **Specify 7**: Live web application instance for validation and user acceptance testing.
    *   **MariaDB**: Database container acting as the staging storage.
    *   **Migration Runners**: Python workers executing the ETL pipeline.
    *   **Nginx/Ingress**: For routing traffic to the Specify interface.
*   **Target Resources**: Storage (NIRD) and Compute (NIRD Service Platform).

### Network Configuration (Firewall Whitelisting)

To allow migration scripts running on Sigma2 to connect to the MUSIT Oracle database, the following Sigma2 IP ranges must be whitelisted in the USIT/MUSIT firewall.

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

## AWS Production

The final destination for the data is a Specify instance hosted on AWS.

*   **Region**: France (EU West / Paris likely).
*   **Role**: Production Specify instance.
*   **Synchronization**: Automated sync from the Sigma2 staging environment.
