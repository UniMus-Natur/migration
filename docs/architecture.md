---
layout: default
title: Architecture
nav_order: 2
---

# Architecture


This section describes the high-level architecture of the migration process.

## Data Flow Overview

The migration follows a pipeline approach moving data from the legacy oracle system to the new Specify cloud instance.

```mermaid
graph LR
    A["MUSIT (Oracle DB)"] -->|Pull & Transform| B("Python Migration Scripts (Sigma2)")
    B -->|Load| C["Specify Staging (MariaDB @ Sigma2)"]
    C -->|Sync| D["Specify Production (AWS France)"]
```

### Components

1.  **Source System**: MUSIT (Oracle Database).
2.  **Migration Logic**: Custom Python scripts executed on **Sigma2**. They pull data from Oracle and use the **Specify 7 Django ORM** to load it into the staging database, keeping the migration code clean and readable.
3.  **Staging Environment**: 
    *   **Location**: Sigma2 (NIRD/NIRD Service Platform).
    *   **Database**: MariaDB.
    *   **Purpose**: Intermediate staging area to validate data structure and content before pushing to production.
4.  **Production Environment**:
    *   **Location**: AWS (France Region).
    *   **System**: Live Specify instance.
