#!/bin/bash

# Usage: ./proxy_db.sh [oracle|mariadb]

case "$1" in
    oracle)
        echo "Starting Oracle Proxy (Prod specific)..."
        # Forward local 1553 to remote Oracle
        # Note: You might need to adjust host/port based on environment (Prod vs Test)
        # Prod: dbora-musit-prod03.uio.no:1553
        # Test: dbora-musit-utv03.uio.no:1553
        export ORACLE_HOST=${ORACLE_HOST:-dbora-musit-prod03.uio.no}
        export ORACLE_PORT=${ORACLE_PORT:-1553}
        echo "Forwarding 0.0.0.0:$ORACLE_PORT -> $ORACLE_HOST:$ORACLE_PORT"
        socat TCP-LISTEN:$ORACLE_PORT,fork TCP:$ORACLE_HOST:$ORACLE_PORT &
        ;;
    mariadb)
        echo "Starting MariaDB Proxy..."
        # Specify7 MariaDB service in the cluster
        # Typically: <release-name>-mariadb
        export MARIADB_HOST=${MARIADB_HOST:-specify7-mariadb}
        export MARIADB_PORT=${MARIADB_PORT:-3306}
        echo "Forwarding 0.0.0.0:3306 -> $MARIADB_HOST:$MARIADB_PORT"
        socat TCP-LISTEN:3306,fork TCP:$MARIADB_HOST:$MARIADB_PORT &
        ;;
    *)
        echo "Usage: $0 {oracle|mariadb}"
        exit 1
        ;;
esac
