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
# Requires: kubectl. Every kubectl call uses --context=nird (the NIRD cluster); the shell's
# current context is ignored. Oracle DB hosts are only reachable from the cluster; this script
# starts socat in the Prefect dev-worker pod (migration image), then port-forwards those pod
# ports to localhost.
#
# Optional env:
#   PF_ORACLE_PROXY_POD         pod name (default: first pod with label component=prefect-dev-worker)
#   PF_ORACLE_PROXY_CONTAINER   container name (default: dev-worker)
#
# NIRD auth note:
#   Context `nird` uses exec plugin `nird-toolkit-auth-helper`, which re-validates the
#   cached OIDC token (UserInfo) on every kubectl process start. Spawning several
#   kubectl port-forwards in parallel reliably yields Unauthorized against the API
#   auth path. This script warms credentials once, then starts forwards one at a time.

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
[[ -z ${_PF_LOGS+x} ]] && _PF_LOGS=()

# Staging lives on the NIRD cluster. Do not follow the shell's current kube context.
_pf_kubectl() {
    command kubectl --context=nird "$@"
}

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

# Serial preflight: one kubectl call so nird-toolkit-auth-helper / API auth settle
# before we start long-lived port-forwards. Retries briefly for transient Unauthorized.
_pf_warm_auth() {
    local attempt err
    err="$(mktemp -t pf-auth.XXXXXX)"
    for attempt in 1 2 3 4 5; do
        if _pf_kubectl auth can-i get pods >/dev/null 2>"$err"; then
            rm -f "$err"
            return 0
        fi
        sleep "$attempt"
    done
    echo "  ✗ NIRD kubectl auth failed (nird-toolkit-auth-helper / API Unauthorized)."
    echo "     Re-login if needed, then retry. Last error:"
    sed 's/^/     /' "$err" >&2 || true
    rm -f "$err"
    return 1
}

# Start one port-forward, wait until local port listens (serial — avoids auth races).
# Usage: _pf_start_forward <label> <local_port> [extra local ports...] -- <kubectl port-forward args...>
_pf_start_forward() {
    local label="$1"
    shift
    local ports=()
    local args=()
    local port pid log i ok
    while [[ $# -gt 0 && "$1" != "--" ]]; do
        ports+=("$1")
        shift
    done
    shift # --
    args=("$@")

    log="$(mktemp -t "pf-${ports[0]}.XXXXXX")"
    _pf_kubectl port-forward "${args[@]}" >"$log" 2>&1 &
    pid=$!
    _PF_PIDS+=("$pid")
    _PF_LOGS+=("$log")

    ok=false
    for i in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        ok=true
        for port in "${ports[@]}"; do
            if ! _pf_tcp_port_listening "$port"; then
                ok=false
                break
            fi
        done
        if $ok; then
            return 0
        fi
        sleep 0.5
    done

    if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null || true
        echo "  ✗ $label failed to start (kubectl exited)."
    else
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        echo "  ✗ $label did not open localhost:${ports[*]} in time."
    fi
    if [[ -s "$log" ]]; then
        sed 's/^/     /' "$log" >&2
    fi
    return 1
}

_pf_forward_mariadb() {
    if _pf_tcp_port_listening 3306; then
        echo "  MariaDB        svc/specify7-mariadb        → localhost:3306 (already in use – skip)"
        return
    fi
    echo "  MariaDB        svc/specify7-mariadb        → localhost:3306"
    _pf_start_forward "MariaDB" 3306 -- svc/specify7-mariadb 3306:3306
}

_pf_forward_prefect() {
    if _pf_tcp_port_listening 4200; then
        echo "  Prefect        svc/specify7-prefect-server  → localhost:4200 (already in use – skip)"
        return
    fi
    echo "  Prefect        svc/specify7-prefect-server  → localhost:4200"
    _pf_start_forward "Prefect" 4200 -- svc/specify7-prefect-server 4200:4200
}

_pf_forward_backend() {
    if _pf_tcp_port_listening 8000; then
        echo "  Specify7 API   svc/specify7-backend         → localhost:8000 (already in use – skip)"
        return
    fi
    echo "  Specify7 API   svc/specify7-backend         → localhost:8000"
    _pf_start_forward "Specify7 API" 8000 -- svc/specify7-backend 8000:8000
}

_pf_forward_oracle() {
    local pod ct phase err
    ct="${PF_ORACLE_PROXY_CONTAINER:-dev-worker}"
    pod="${PF_ORACLE_PROXY_POD:-}"
    err="$(mktemp -t pf-oracle.XXXXXX)"
    if [[ -z "$pod" ]]; then
        pod=$(_pf_kubectl get pods -l component=prefect-dev-worker -o jsonpath='{.items[0].metadata.name}' 2>"$err" || true)
    fi
    if [[ -z "$pod" ]]; then
        echo "  ⚠ Oracle: no proxy pod (label component=prefect-dev-worker)."
        if [[ -s "$err" ]]; then
            sed 's/^/     /' "$err" >&2
        else
            echo "     Enable chart values prefect.devWorker.enabled, or: export PF_ORACLE_PROXY_POD=<pod>"
        fi
        rm -f "$err"
        return
    fi
    rm -f "$err"
    phase=$(_pf_kubectl get pod "$pod" -o jsonpath='{.status.phase}' 2>/dev/null || true)
    if [[ "$phase" != "Running" ]]; then
        echo "  ⚠ Oracle: pod $pod is not Running (status.phase=${phase:-unknown})"
        return
    fi

    if _pf_tcp_port_listening 1553 || _pf_tcp_port_listening 1554; then
        echo "  Oracle PROD+TEST  localhost:1553 / :1554 (already in use – skip)"
        return
    fi

    echo "  Oracle PROD+TEST  pod/$pod ($ct) → localhost:1553 / :1554"
    _pf_kubectl exec "$pod" -c "$ct" -- bash -c \
        'socat TCP-LISTEN:1553,fork,reuseaddr TCP:dbora-musit-prod03.uio.no:1553 & socat TCP-LISTEN:1554,fork,reuseaddr TCP:dbora-musit-utv03.uio.no:1553 & wait' \
        </dev/null &
    _PF_PIDS+=($!)
    sleep 1
    _pf_start_forward "Oracle" 1553 1554 -- "pod/$pod" 1553:1553 1554:1554
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

#
# Dump ALL Specify7 rows linked to a catalog number in a migrated collection
# (default: O-V).
#
#   specify_catalog_dump --catalog "O-V-123456"
#   specify_catalog_dump --catalog "O-V-123456" --collection-code O-V
#   specify_catalog_dump --catalog "O-V-123456" --output specify.json
#
specify_catalog_dump() {
    python3 "$_PF_SCRIPT_DIR/specify_catalog_dump.py" "$@"
}

#
# Compare Oracle source data with Specify7 sink for one or more catalog numbers.
# Runs both dump scripts under the hood and outputs a terminal summary, JSON
# report, and/or a Markdown evidence card suitable for LLM judgement.
#
#   migration_compare --catalog "O-V-123456" --collection-code O-V
#   migration_compare --fixture scripts/test_fixtures.yaml
#   migration_compare --fixture scripts/test_fixtures.yaml --format all --output-dir /tmp/reports
#
migration_compare() {
    python3 "$_PF_SCRIPT_DIR/migration_compare.py" "$@"
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
    for log in "${_PF_LOGS[@]}"; do
        rm -f "$log"
    done
    _PF_LOGS=()
    unset PREFECT_API_URL
    echo "Done."
}

# --- Parse targets -----------------------------------------------------------

_pf_targets=("$@")
[[ ${#_pf_targets[@]} -eq 0 ]] && _pf_targets=("all")

echo "Starting port-forwards..."
if ! _pf_warm_auth; then
    echo ""
    echo "Aborted: no port-forwards started."
    return 1 2>/dev/null || exit 1
fi

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
_pf_any=false
for pid in "${_PF_PIDS[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
        _pf_any=true
        break
    fi
done
if $_pf_any; then
    echo "Forwards running in background. Run 'pf_stop' to tear down."
else
    echo "No port-forwards are running (all failed or skipped)."
fi
echo "Oracle SQL helper:      oracle_sql [--env prod|test] [--csv] \"<SQL>\""
echo "  PROD needs Oracle Instant Client (thick mode) on this machine — see scripts/oracle_sql.py"
echo "Oracle catalog dump:    oracle_catalog_dump --catalog \"O-V-123456\" [--env prod|test] [--output file.json]"
echo "  Dumps ALL connected Oracle data for a catalog number as JSON — see scripts/oracle_catalog_dump.py"
echo "Specify catalog dump:   specify_catalog_dump --catalog \"O-V-123456\" [--collection-code O-V] [--output file.json]"
echo "  Dumps ALL connected Specify rows for a catalog number as JSON — see scripts/specify_catalog_dump.py"
echo "Migration compare:      migration_compare --catalog \"O-V-123456\" [--collection-code O-V] [--format terminal|json|md|all]"
echo "  Compare Oracle vs Specify data; use --fixture scripts/test_fixtures.yaml for batch runs — see scripts/migration_compare.py"
echo "Specify API helper:     specify_api [--collection O-V] [--text-fields|--geography-tree] [<path>]"
echo "  Requires 'backend' forward (localhost:8000) — see scripts/specify_api.py"
