from prefect import flow, task, get_run_logger

from flows.lib.mariadb_connectivity import (
    connect_and_ping_mariadb,
    get_mariadb_config_from_env,
)
from flows.lib.oracle_connectivity import (
    connect_and_ping_oracle,
    get_oracle_config_from_env,
    initialize_oracle_client,
)
from flows.lib.s3_connectivity import validate_s3_connectivity


@task(retries=2, retry_delay_seconds=5)
def run_oracle_connectivity_check(env_prefix: str) -> None:
    logger = get_run_logger()
    initialize_oracle_client(log=logger.info)
    config = get_oracle_config_from_env(env_prefix)
    success = connect_and_ping_oracle(
        config=config,
        log_info=logger.info,
        log_error=logger.error,
    )
    if not success:
        logger.error(f"Oracle {env_prefix} connectivity check failed.")
        raise Exception("Failed to verify Oracle connection")


@task(retries=2, retry_delay_seconds=3)
def run_mariadb_connectivity_check() -> None:
    logger = get_run_logger()
    config = get_mariadb_config_from_env()
    success = connect_and_ping_mariadb(
        config=config,
        log_info=logger.info,
        log_error=logger.error,
    )
    if not success:
        logger.error("MariaDB connectivity check failed.")
        raise Exception("Failed to verify MariaDB connection")


@task(retries=1, retry_delay_seconds=3)
def run_s3_connectivity_check() -> None:
    logger = get_run_logger()
    validate_s3_connectivity(write_check=True)
    logger.info("S3 connectivity check succeeded (head_bucket + write/delete probe).")


@flow(
    name="Infrastructure Prod Check",
    description="Runs Prod infrastructure connectivity checks (Oracle, MariaDB, S3)",
)
def infrastructure_prod_check_flow():
    # Keep this list-based structure so adding TEST back is a one-line change.
    for target in ["PROD"]:
        run_oracle_connectivity_check(target)
    run_mariadb_connectivity_check()
    run_s3_connectivity_check()


@flow(
    name="Infrastructure Test Check",
    description="Runs Test infrastructure connectivity checks (Oracle, MariaDB, S3)",
)
def infrastructure_test_check_flow():
    run_oracle_connectivity_check("TEST")
    run_mariadb_connectivity_check()
    run_s3_connectivity_check()

if __name__ == "__main__":
    infrastructure_prod_check_flow()
