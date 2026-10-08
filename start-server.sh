#!/usr/bin/env bash
# screen-control launcher (Linux). Mirrors start-server.bat.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    echo "[ERROR] .venv not found - Run ./install-linux.sh first" >&2
    exit 1
fi

exec .venv/bin/python server.py "$@"
