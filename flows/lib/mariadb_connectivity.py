import os
from collections.abc import Callable
from dataclasses import dataclass

import MySQLdb

@dataclass(frozen=True)
class MariaDBConfig:
    host: str
    port: int
    database: str
    user: str
    password: str

def get_mariadb_config_from_env() -> MariaDBConfig:
    host = os.getenv("DATABASE_HOST", "specify7-mariadb")
    port_str = os.getenv("DATABASE_PORT", "3306")
    database = os.getenv("DATABASE_NAME", "specify")
    user = os.getenv("APP_USER_NAME", "bn_specify")
    password = os.getenv("APP_USER_PASSWORD", "password")

    return MariaDBConfig(
        host=host,
        port=int(port_str),
        database=database,
        user=user,
        password=password,
    )

def connect_and_ping_mariadb(
    config: MariaDBConfig,
    log_info: Callable[[str], None] | None = None,
    log_error: Callable[[str], None] | None = None,
) -> bool:
    if log_info:
        log_info(f"Attempting connection to MariaDB Database {config.database} at {config.host}:{config.port}")

    try:
        connection = MySQLdb.connect(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            database=config.database,
        )
        if log_info:
            log_info("Successfully established connection to MariaDB.")

        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            result = cursor.fetchone()
            if result and log_info:
                log_info(f"Connected successfully! MariaDB Version: {result[0]}")

        connection.close()
        return True
    except MySQLdb.Error as exc:
        if log_error:
            log_error(f"MariaDB Error: {exc}")
        return False
    except Exception as exc:
        if log_error:
            log_error(f"Failed to connect to MariaDB: {str(exc)}")
        return False
