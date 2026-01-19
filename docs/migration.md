# Migration Strategy

This document details the technical implementation of the migration from MUSIT to Specify.

## Strategy

We are using an Extract-Transform-Load (ETL) pattern.

### 1. Connect and Extract
*   **Source**: MUSIT Oracle Database.
*   **Tool**: **SQLAlchemy** with `oracledb` driver.
*   **Method**: Select queries (via Pandas `read_sql` or SQLAlchemy Core) to retrieve data from legacy tables.

### 2. Transform
*   **Logic**: Python scripts will map MUSIT schema fields to Specify schema.
*   **Data Cleaning**: Any necessary data cleanup will happen at this stage.

### 3. Load to Staging
*   **Target**: MariaDB on Sigma2.
*   **Tool**: SQLAlchemy (likely via Pandas `to_sql`).
*   **Schema**: Specify Schema.

### 4. Sync to Production
*   **Mechanism**: Database replication or dump/restore methodology (TBD) to move data from Sigma2 to AWS.
