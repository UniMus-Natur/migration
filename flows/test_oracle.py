import os
import oracledb
from prefect import flow, task, get_run_logger

_ORACLE_CLIENT_INITIALIZED = False

def initialize_oracle_client() -> None:
    global _ORACLE_CLIENT_INITIALIZED
    if _ORACLE_CLIENT_INITIALIZED:
        return

    logger = get_run_logger()
    use_thick = os.getenv("ORACLE_USE_THICK_MODE", "true").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if not use_thick:
        logger.info("Using python-oracledb thin mode.")
        _ORACLE_CLIENT_INITIALIZED = True
        return

    lib_dir = os.getenv("ORACLE_CLIENT_LIB_DIR")
    if lib_dir:
        oracledb.init_oracle_client(lib_dir=lib_dir)
        logger.info(f"Initialized python-oracledb thick mode with lib_dir={lib_dir}")
    else:
        oracledb.init_oracle_client()
        logger.info("Initialized python-oracledb thick mode.")
    _ORACLE_CLIENT_INITIALIZED = True

@task(retries=2, retry_delay_seconds=5)
def test_oracle_connection(host: str, port: str, service_name: str, user: str, password: str) -> bool:
    logger = get_run_logger()
    logger.info(f"Attempting connection to Oracle Database at {host}:{port}/{service_name}")
    
    dsn = f"{host}:{port}/{service_name}"
    
    try:
        initialize_oracle_client()
        connection = oracledb.connect(user=user, password=password, dsn=dsn)
        logger.info("Successfully established connection to Oracle.")
        
        with connection.cursor() as cursor:
            cursor.execute("SELECT sysdate FROM dual")
            result = cursor.fetchone()
            if result:
                logger.info(f"Connected successfully! Current DB Time: {result[0]}")
                
        connection.close()
        return True
    except oracledb.DatabaseError as e:
        error, = e.args
        logger.error(f"Oracle-specific Database Error: {error.message}")
        return False
    except Exception as e:
        logger.error(f"Failed to connect to Oracle: {str(e)}")
        return False

def run_oracle_connectivity_check(env_prefix: str) -> None:
    logger = get_run_logger()

    user_key = f"ORACLE_{env_prefix}_USER"
    password_key = f"ORACLE_{env_prefix}_PASSWORD"
    host_key = f"ORACLE_{env_prefix}_HOST"
    port_key = f"ORACLE_{env_prefix}_PORT"
    service_key = f"ORACLE_{env_prefix}_SERVICE"

    user = os.getenv(user_key)
    password = os.getenv(password_key)
    host = os.getenv(host_key)
    port = os.getenv(port_key, "1553")
    service = os.getenv(service_key)

    if not all([user, password, host, service]):
        raise ValueError(
            "Missing Oracle credentials "
            f"({user_key}, {password_key}, {host_key}, {service_key})"
        )

    success = test_oracle_connection(
        host=host,
        port=port,
        service_name=service,
        user=user,
        password=password
    )

    if not success:
        logger.error(f"Oracle {env_prefix} connectivity check failed.")
        raise Exception("Failed to verify Oracle connection")

@flow(
    name="Oracle Connectivity Check Test",
    description="Verifies Oracle TEST connectivity using ORACLE_TEST_* credentials",
)
def oracle_connectivity_test_flow():
    run_oracle_connectivity_check("TEST")

@flow(
    name="Oracle Connectivity Check Prod",
    description="Verifies Oracle PROD connectivity using ORACLE_PROD_* credentials",
)
def oracle_connectivity_prod_flow():
    run_oracle_connectivity_check("PROD")

if __name__ == "__main__":
    oracle_connectivity_test_flow()
