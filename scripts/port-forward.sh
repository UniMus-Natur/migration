#!/bin/bash

# Port-forward cluster services and proxy Oracle databases to localhost.
#
# Usage (source it so env vars persist and pf_stop is available):
#
#   source scripts/port-forward.sh              # forward everything
#   source scripts/port-forward.sh prefect db   # combine shortcuts
#   pf_stop                                     # tear down when done
#
# Targets:
#   mariadb    MariaDB         localhost:3306
#   prefect    Prefect UI/API  localhost:4200  (+ sets PREFECT_API_URL)
#   backend    Specify7 API    localhost:8000
#   oracle     Oracle prod+test via socat  localhost:1553 / localhost:1554
#   db         shorthand for mariadb + oracle
#   all        everything (default)
#
# Requires: kubectl, socat (for Oracle only)

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

_PF_PIDS=()
_PF_PREFECT_STARTED=false

_pf_forward_mariadb() {
    echo "  MariaDB        svc/specify7-mariadb        → localhost:3306"
    kubectl port-forward svc/specify7-mariadb 3306:3306 >/dev/null 2>&1 &
    _PF_PIDS+=($!)
}

_pf_forward_prefect() {
    echo "  Prefect        svc/specify7-prefect-server  → localhost:4200"
    kubectl port-forward svc/specify7-prefect-server 4200:4200 >/dev/null 2>&1 &
    _PF_PIDS+=($!)
    _PF_PREFECT_STARTED=true
}

_pf_forward_backend() {
    echo "  Specify7 API   svc/specify7-backend         → localhost:8000"
    kubectl port-forward svc/specify7-backend 8000:8000 >/dev/null 2>&1 &
    _PF_PIDS+=($!)
}

_pf_forward_oracle() {
    if ! command -v socat &>/dev/null; then
        echo "  ⚠ socat not found – skipping Oracle proxies"
        return
    fi
    echo "  Oracle PROD    dbora-musit-prod03.uio.no:1553  → localhost:1553"
    socat TCP-LISTEN:1553,fork,reuseaddr TCP:dbora-musit-prod03.uio.no:1553 &
    _PF_PIDS+=($!)

    echo "  Oracle TEST    dbora-musit-utv03.uio.no:1553   → localhost:1554"
    socat TCP-LISTEN:1554,fork,reuseaddr TCP:dbora-musit-utv03.uio.no:1553 &
    _PF_PIDS+=($!)
}

pf_stop() {
    echo "Shutting down port-forwards..."
    for pid in "${_PF_PIDS[@]}"; do
        kill "$pid" 2>/dev/null
    done
    _PF_PIDS=()
    _PF_PREFECT_STARTED=false
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

if $_PF_PREFECT_STARTED; then
    export PREFECT_API_URL="http://localhost:4200/api"
    echo ""
    echo "  export PREFECT_API_URL=$PREFECT_API_URL"
fi

echo ""
echo "Forwards running in background. Run 'pf_stop' to tear down."
