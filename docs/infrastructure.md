# Infrastructure

## Sigma2 Resources

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
