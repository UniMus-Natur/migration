import os
from collections.abc import Callable
from dataclasses import dataclass

import oracledb

_ORACLE_CLIENT_INITIALIZED = False


@dataclass(frozen=True)
class OracleConfig:
    env_prefix: str
    host: str
    port: str
    service_name: str
    user: str
    password: str


def initialize_oracle_client(log: Callable[[str], None] | None = None) -> None:
    global _ORACLE_CLIENT_INITIALIZED
    if _ORACLE_CLIENT_INITIALIZED:
        return

    use_thick = os.getenv("ORACLE_USE_THICK_MODE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not use_thick:
        if log:
            log("Using python-oracledb thin mode.")
        _ORACLE_CLIENT_INITIALIZED = True
        return

    lib_dir = os.getenv("ORACLE_CLIENT_LIB_DIR")
    if lib_dir:
        oracledb.init_oracle_client(lib_dir=lib_dir)
        if log:
            log(f"Initialized python-oracledb thick mode with lib_dir={lib_dir}")
    else:
        oracledb.init_oracle_client()
        if log:
            log("Initialized python-oracledb thick mode.")
    _ORACLE_CLIENT_INITIALIZED = True


def get_oracle_config_from_env(env_prefix: str) -> OracleConfig:
    prefix = env_prefix.strip().upper()
    user_key = f"ORACLE_{prefix}_USER"
    password_key = f"ORACLE_{prefix}_PASSWORD"
    host_key = f"ORACLE_{prefix}_HOST"
    port_key = f"ORACLE_{prefix}_PORT"
    service_key = f"ORACLE_{prefix}_SERVICE"

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

    return OracleConfig(
        env_prefix=prefix,
        host=host,
        port=port,
        service_name=service,
        user=user,
        password=password,
    )


def connect_and_ping_oracle(
    config: OracleConfig,
    log_info: Callable[[str], None] | None = None,
    log_error: Callable[[str], None] | None = None,
) -> bool:
    dsn = f"{config.host}:{config.port}/{config.service_name}"
    if log_info:
        log_info(f"Attempting connection to Oracle Database at {dsn}")

    try:
        connection = oracledb.connect(
            user=config.user, password=config.password, dsn=dsn
        )
        if log_info:
            log_info("Successfully established connection to Oracle.")

        with connection.cursor() as cursor:
            cursor.execute("SELECT sysdate FROM dual")
            result = cursor.fetchone()
            if result and log_info:
                log_info(f"Connected successfully! Current DB Time: {result[0]}")

        connection.close()
        return True
    except oracledb.DatabaseError as exc:
        error, = exc.args
        if log_error:
            log_error(f"Oracle-specific Database Error: {error.message}")
        return False
    except Exception as exc:
        if log_error:
            log_error(f"Failed to connect to Oracle: {str(exc)}")
        return False
