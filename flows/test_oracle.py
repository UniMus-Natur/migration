import os
import oracledb
from prefect import flow, task, get_run_logger

@task(retries=2, retry_delay_seconds=5)
def test_oracle_connection(host: str, port: str, service_name: str, user: str, password: str) -> bool:
    logger = get_run_logger()
    logger.info(f"Attempting connection to Oracle Database at {host}:{port}/{service_name}")
    
    dsn = f"{host}:{port}/{service_name}"
    
    try:
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

@flow(name="Test Oracle Connectivity", description="Verifies connection to the Oracle Test database using environment credentials")
def test_oracle_connectivity_flow():
    logger = get_run_logger()
    
    # Retrieve configuration from environment variables (as available in the pod)
    user = os.getenv("ORACLE_TEST_USER")
    password = os.getenv("ORACLE_TEST_PASSWORD")
    host = os.getenv("ORACLE_TEST_HOST")
    port = os.getenv("ORACLE_TEST_PORT", "1553")
    service = os.getenv("ORACLE_TEST_SERVICE")
    
    if not all([user, password, host, service]):
        logger.error("Missing required Oracle credentials in the environment variables.")
        raise ValueError("Missing Oracle credentials (ORACLE_TEST_USER, ORACLE_TEST_PASSWORD, ORACLE_TEST_HOST, ORACLE_TEST_SERVICE)")
        
    success = test_oracle_connection(
        host=host,
        port=port,
        service_name=service,
        user=user,
        password=password
    )
    
    if not success:
        raise Exception("Failed to verify Oracle connection")
        
if __name__ == "__main__":
    test_oracle_connectivity_flow()
