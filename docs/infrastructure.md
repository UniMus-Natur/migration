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
*   **Target Resources**: Storage (NIRD) and Compute (likely NIRD Service Platform or small scale testing).
*   **Next Deadline**: **Monday, February 23, 2026** (for allocation starting April 1, 2026).
*   **Action Item**: Verify requirements and submit application before the deadline.

### Resource Specs
*   **Database**: MariaDB (running on Sigma2 infrastructure).
*   **Scripts**: Python scripts running on Sigma2 compute nodes to perform the migration.

## AWS Production

The final destination for the data is a Specify instance hosted on AWS.

*   **Region**: France (EU West / Paris likely).
*   **Role**: Production Specify instance.
*   **Synchronization**: Automated sync from the Sigma2 staging environment.
