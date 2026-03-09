from prefect import flow, task, get_run_logger

from flows.lib.oracle_connectivity import (
    connect_and_ping_oracle,
    get_oracle_config_from_env,
    initialize_oracle_client,
)


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


@flow(
    name="Oracle Connectivity Prod Check",
    description="Runs Oracle connectivity check for PROD credentials",
)
def oracle_connectivity_prod_flow():
    # Keep this list-based structure so adding TEST back is a one-line change.
    for target in ["PROD"]:
        run_oracle_connectivity_check(target)


@flow(
    name="Oracle Connectivity Test Check",
    description="Runs Oracle connectivity check for TEST credentials",
)
def oracle_connectivity_test_flow():
    run_oracle_connectivity_check("TEST")

if __name__ == "__main__":
    oracle_connectivity_prod_flow()
