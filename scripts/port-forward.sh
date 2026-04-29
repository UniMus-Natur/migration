#!/bin/bash

# Port-forward cluster services and proxy Oracle databases to localhost.
#
# Usage (source it so env vars persist and pf_stop is available):
#
#   source scripts/port-forward.sh              # forward everything
#   source scripts/port-forward.sh prefect db   # combine shortcuts
#   pf_stop                                     # tear down when done
#
# Safe to source again: skips any localhost port that already has a listener
# (avoids duplicate kubectl port-forwards and keeps pf_stop tracking intact).
#
# Targets:
#   mariadb    MariaDB         localhost:3306
#   prefect    Prefect UI/API  localhost:4200  (+ sets PREFECT_API_URL)
#   backend    Specify7 API    localhost:8000
#   oracle     Oracle prod+test  localhost:1553 / :1554  (socat inside cluster pod + kubectl port-forward)
#   db         shorthand for mariadb + oracle
#   all        everything (default)
#
# Requires: kubectl. Oracle DB hosts are only reachable from the cluster; this script starts socat in
# the Prefect dev-worker pod (migration image), then port-forwards those pod ports to localhost.
#
# Optional env:
#   PF_ORACLE_PROXY_POD         pod name (default: first pod with label component=prefect-dev-worker)
#   PF_ORACLE_PROXY_CONTAINER   container name (default: dev-worker)

# Capture scripts directory at source time so oracle_sql() can find oracle_sql.py
_PF_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# Guard: warn if not sourced
_pf_sourced=false
if [[ "${BASH_SOURCE[0]}" != "$0" ]] 2>/dev/null; then
    _pf_sourced=true
elif [[ -n "$ZSH_EVAL_CONTEXT" && "$ZSH_EVAL_CONTEXT" =~ :file$ ]]; then
    _pf_sourced=true
fi

if ! $_pf_sourced; then
    echo "Hint: source this script so env vars persist in your shell:"
    echo ""
    echo "  source $0 $*"
    echo ""
fi

# Activate .venv if present and not already active
_pf_venv="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)/.venv"
if [[ -f "$_pf_venv/bin/activate" && "$VIRTUAL_ENV" != "$_pf_venv" ]]; then
    source "$_pf_venv/bin/activate"
    echo "Activated virtualenv: $VIRTUAL_ENV"
fi

# Populated only for processes started in this shell; do not clear on repeat source
# (so pf_stop still kills the first batch). Unset until first use or after pf_stop clears it.
[[ -z ${_PF_PIDS+x} ]] && _PF_PIDS=()

# Return 0 if something is listening on 127.0.0.1:port (IPv4).
_pf_tcp_port_listening() {
    local port="${1:?}"
    if command -v lsof &>/dev/null; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN &>/dev/null
        return $?
    fi
    (echo >/dev/tcp/127.0.0.1/"$port") &>/dev/null
    return $?
}

_pf_forward_mariadb() {
    if _pf_tcp_port_listening 3306; then
        echo "  MariaDB        svc/specify7-mariadb        → localhost:3306 (already in use – skip)"
        return
    fi
    echo "  MariaDB        svc/specify7-mariadb        → localhost:3306"
    kubectl port-forward svc/specify7-mariadb 3306:3306 >/dev/null 2>&1 &
    _PF_PIDS+=($!)
}

_pf_forward_prefect() {
    if _pf_tcp_port_listening 4200; then
        echo "  Prefect        svc/specify7-prefect-server  → localhost:4200 (already in use – skip)"
        return
    fi
    echo "  Prefect        svc/specify7-prefect-server  → localhost:4200"
    kubectl port-forward svc/specify7-prefect-server 4200:4200 >/dev/null 2>&1 &
    _PF_PIDS+=($!)
}

_pf_forward_backend() {
    if _pf_tcp_port_listening 8000; then
        echo "  Specify7 API   svc/specify7-backend         → localhost:8000 (already in use – skip)"
        return
    fi
    echo "  Specify7 API   svc/specify7-backend         → localhost:8000"
    kubectl port-forward svc/specify7-backend 8000:8000 >/dev/null 2>&1 &
    _PF_PIDS+=($!)
}

_pf_forward_oracle() {
    local pod ct phase
    ct="${PF_ORACLE_PROXY_CONTAINER:-dev-worker}"
    pod="${PF_ORACLE_PROXY_POD:-}"
    if [[ -z "$pod" ]]; then
        pod=$(kubectl get pods -l component=prefect-dev-worker -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    fi
    if [[ -z "$pod" ]]; then
        echo "  ⚠ Oracle: no proxy pod (label component=prefect-dev-worker)."
        echo "     Enable chart values prefect.devWorker.enabled, or: export PF_ORACLE_PROXY_POD=<pod>"
        return
    fi
    phase=$(kubectl get pod "$pod" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    if [[ "$phase" != "Running" ]]; then
        echo "  ⚠ Oracle: pod $pod is not Running (status.phase=${phase:-unknown})"
        return
    fi

    if _pf_tcp_port_listening 1553 || _pf_tcp_port_listening 1554; then
        echo "  Oracle PROD+TEST  localhost:1553 / :1554 (already in use – skip)"
        return
    fi

    echo "  Oracle PROD+TEST  pod/$pod ($ct) → localhost:1553 / :1554"
    kubectl exec "$pod" -c "$ct" -- bash -c \
        'socat TCP-LISTEN:1553,fork,reuseaddr TCP:dbora-musit-prod03.uio.no:1553 & socat TCP-LISTEN:1554,fork,reuseaddr TCP:dbora-musit-utv03.uio.no:1553 & wait' \
        </dev/null &
    _PF_PIDS+=($!)
    sleep 1
    kubectl port-forward "pod/$pod" 1553:1553 1554:1554 >/dev/null 2>&1 &
    _PF_PIDS+=($!)
}

_pf_targets_include_prefect() {
    local t
    for t in "${_pf_targets[@]}"; do
        [[ "$t" == "all" || "$t" == "prefect" ]] && return 0
    done
    return 1
}

# Run SQL on Oracle PROD (default) or TEST via the port-forwarded localhost tunnel.
#
#   oracle_sql "SELECT sysdate FROM dual"
#   echo "SELECT * FROM some_table WHERE ROWNUM <= 10" | oracle_sql
#   oracle_sql --env test "SELECT 1 FROM dual"
#   oracle_sql --csv "SELECT owner, table_name FROM dba_tables WHERE ROWNUM <= 20"
#   oracle_sql --list-schemas
#
oracle_sql() {
    python3 "$_PF_SCRIPT_DIR/oracle_sql.py" "$@"
}

# Dump ALL Oracle source data for a catalog number as JSON.
#
#   oracle_catalog_dump --catalog "O-V-123456"
#   oracle_catalog_dump --catalog "TRH-V-241112" --env prod
#   oracle_catalog_dump --catalog 241112
#   oracle_catalog_dump --object-id 12345
#   oracle_catalog_dump --catalog "O-V-123456" --output dump.json
#   oracle_catalog_dump --catalog "O-V-123456" --compact | jq .
#
oracle_catalog_dump() {
    python3 "$_PF_SCRIPT_DIR/oracle_catalog_dump.py" "$@"
}

specify_api() {
    python3 "$_PF_SCRIPT_DIR/specify_api.py" "$@"
}

pf_stop() {
    echo "Shutting down port-forwards..."
    for pid in "${_PF_PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    _PF_PIDS=()
    unset PREFECT_API_URL
    echo "Done."
}

# --- Parse targets -----------------------------------------------------------

_pf_targets=("$@")
[[ ${#_pf_targets[@]} -eq 0 ]] && _pf_targets=("all")

echo "Starting port-forwards..."

for _pf_t in "${_pf_targets[@]}"; do
    case "$_pf_t" in
        all)      _pf_forward_mariadb; _pf_forward_prefect; _pf_forward_backend; _pf_forward_oracle ;;
        mariadb)  _pf_forward_mariadb ;;
        prefect)  _pf_forward_prefect ;;
        backend)  _pf_forward_backend ;;
        oracle)   _pf_forward_oracle ;;
        db)       _pf_forward_mariadb; _pf_forward_oracle ;;
        *)        echo "Unknown target: $_pf_t" ;;
    esac
done

if _pf_targets_include_prefect && _pf_tcp_port_listening 4200; then
    export PREFECT_API_URL="http://localhost:4200/api"
    echo ""
    echo "  export PREFECT_API_URL=$PREFECT_API_URL"
fi

echo ""
echo "Forwards running in background. Run 'pf_stop' to tear down."
echo "Oracle SQL helper:      oracle_sql [--env prod|test] [--csv] \"<SQL>\""
echo "  PROD needs Oracle Instant Client (thick mode) on this machine — see scripts/oracle_sql.py"
echo "Oracle catalog dump:    oracle_catalog_dump --catalog \"O-V-123456\" [--env prod|test] [--output file.json]"
echo "  Dumps ALL connected Oracle data for a catalog number as JSON — see scripts/oracle_catalog_dump.py"
echo "Specify API helper:     specify_api [--collection NHM] [--text-fields|--geography-tree] [<path>]"
echo "  Requires 'backend' forward (localhost:8000) — see scripts/specify_api.py"
