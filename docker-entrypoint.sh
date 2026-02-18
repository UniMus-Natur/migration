#!/bin/bash
set -e

echo "Entrypoint: Starting..."

# Function to clear and dump environment
dump_env() {
    local DEST="$1"
    echo "Entrypoint: Dumping environment to $DEST"
    
    # Header
    echo "# Environment variables dumped by entrypoint" > "$DEST"
    
    # Dump using printenv, formatting as export statements
    # Filter out variables that shouldn't be overridden or are session-specific
    printenv | grep -vE '^(HOME|PWD|SHLVL|_|TERM|HOSTNAME|PATH)$' | \
    while read -r line; do
        # Escape quotes in values to avoid syntax errors
        # format: KEY=VALUE -> export KEY='VALUE'
        key=$(echo "$line" | cut -d= -f1)
        val=$(echo "$line" | cut -d= -f2-)
        # We use single quotes for values to minimize expansion issues, replacing single quotes inside with '\''
        echo "export $key='${val//\'/\'\\\'\'}'" >> "$DEST"
    done
}

# 1. /etc/profile.d/99-k8s-env.sh (For login shells)
# This directory is sourced by /etc/profile, usually.
mkdir -p /etc/profile.d
dump_env "/etc/profile.d/99-k8s-env.sh"

# 2. Append to /root/.bashrc (For non-login interactive shells)
# Only if we haven't already added the source command
if ! grep -q "source /etc/profile.d/99-k8s-env.sh" /root/.bashrc; then
    echo "Entrypoint: Sourcing env in /root/.bashrc"
    echo "" >> /root/.bashrc
    echo "# Load Kubernetes environment variables" >> /root/.bashrc
    echo "if [ -f /etc/profile.d/99-k8s-env.sh ]; then" >> /root/.bashrc
    echo "    source /etc/profile.d/99-k8s-env.sh" >> /root/.bashrc
    echo "fi" >> /root/.bashrc
fi

# 3. /etc/environment (For PAM)
# PAM expects KEY=VAL format, not export KEY=VAL
echo "Entrypoint: Dumping to /etc/environment"
printenv | grep -vE '^(HOME|PWD|SHLVL|_|TERM|HOSTNAME|PATH)$' > /etc/environment

echo "Entrypoint: Setup complete. Executing command: $@"
exec "$@"
