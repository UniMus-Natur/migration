#!/bin/bash
set -e

# Port-forward cluster services and proxy Oracle databases to localhost.
#
# Usage:
#   ./port-forward.sh              # forward everything
#   ./port-forward.sh mariadb      # forward only specific targets
#   ./port-forward.sh prefect db   # combine shortcuts
#
# Targets:
#   mariadb    MariaDB         localhost:3306
#   prefect    Prefect UI/API  localhost:4200
#   backend    Specify7 API    localhost:8000
#   oracle     Oracle prod+test via socat  localhost:1553 / localhost:1554
#   db         shorthand for mariadb + oracle
#   all        everything (default)
#
# Requires: kubectl, socat (for Oracle only)
# Stop:     Ctrl-C  (kills all background jobs)

PIDS=()

cleanup() {
    echo ""
    echo "Shutting down port-forwards..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    wait 2>/dev/null
    echo "Done."
}
trap cleanup EXIT INT TERM

forward_mariadb() {
    echo "  MariaDB        svc/specify7-mariadb        → localhost:3306"
    kubectl port-forward svc/specify7-mariadb 3306:3306 >/dev/null 2>&1 &
    PIDS+=($!)
}

forward_prefect() {
    echo "  Prefect        svc/specify7-prefect-server  → localhost:4200"
    kubectl port-forward svc/specify7-prefect-server 4200:4200 >/dev/null 2>&1 &
    PIDS+=($!)
}

forward_backend() {
    echo "  Specify7 API   svc/specify7-backend         → localhost:8000"
    kubectl port-forward svc/specify7-backend 8000:8000 >/dev/null 2>&1 &
    PIDS+=($!)
}

forward_oracle() {
    if ! command -v socat &>/dev/null; then
        echo "  ⚠ socat not found – skipping Oracle proxies"
        return
    fi
    echo "  Oracle PROD    dbora-musit-prod03.uio.no:1553  → localhost:1553"
    socat TCP-LISTEN:1553,fork,reuseaddr TCP:dbora-musit-prod03.uio.no:1553 &
    PIDS+=($!)

    echo "  Oracle TEST    dbora-musit-utv03.uio.no:1553   → localhost:1554"
    socat TCP-LISTEN:1554,fork,reuseaddr TCP:dbora-musit-utv03.uio.no:1553 &
    PIDS+=($!)
}

# --- Parse targets -----------------------------------------------------------

targets=("$@")
[[ ${#targets[@]} -eq 0 ]] && targets=("all")

for t in "${targets[@]}"; do
    case "$t" in
        all)      forward_mariadb; forward_prefect; forward_backend; forward_oracle ;;
        mariadb)  forward_mariadb ;;
        prefect)  forward_prefect ;;
        backend)  forward_backend ;;
        oracle)   forward_oracle ;;
        db)       forward_mariadb; forward_oracle ;;
        *)        echo "Unknown target: $t"; exit 1 ;;
    esac
done

echo ""
echo "All forwards running. Press Ctrl-C to stop."
echo ""

wait
