#!/bin/bash
set -e

# Dump environment variables to /etc/environment so they are available to SSH sessions
# avoiding duplicates and filtering out potentially problematic ones if needed
env | grep -v "HOME" | grep -v "PWD" | grep -v "SHLVL" | grep -v "_" > /etc/environment

# Execute the passed command
exec "$@"
