#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# install.py uses only the Python standard library, so it needs no virtualenv or
# dependency install — run it directly. Set PYTHON=... to pick a specific
# interpreter (defaults to python3 on PATH).
PYTHON_BIN="${PYTHON:-python3}"
exec "$PYTHON_BIN" "$SCRIPT_DIR/install.py" "$@"
