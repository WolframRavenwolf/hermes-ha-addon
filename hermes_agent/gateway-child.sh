#!/bin/bash
# Exec one per-slot supervisor that owns the gateway and all descendants.
set -euo pipefail

if [ "$#" -ne 5 ]; then
    echo "gateway-child.sh: expected PYTHON SUPERVISOR LAUNCHER READY_PATH PARENT_PID" >&2
    exit 64
fi

python_path="$1"
supervisor="$2"
launcher="$3"
ready_path="$4"
parent_pid="$5"

exec "$python_path" "$supervisor" \
    "$launcher" "$ready_path" "$parent_pid"
