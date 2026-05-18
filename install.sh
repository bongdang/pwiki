#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VENV_DIR="${PWIKI_INSTALL_VENV:-$SCRIPT_DIR/.venv-install}"

if command -v uv >/dev/null 2>&1 && uv venv "$VENV_DIR" >/dev/null 2>&1; then
  PIP_NO_CACHE_DIR=1 "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/install-requirements.txt"
  exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/install.py" "$@"
fi

# Fall back to stdlib venv when uv is unavailable or fails.
# If `uv venv` leaves a partially-created directory behind, `python3 -m venv`
# can reject it as "directory already exists", so --clear rebuilds it safely.
PYTHON_BIN="${PYTHON:-python3}"
"$PYTHON_BIN" -m venv --clear "$VENV_DIR"
PIP_NO_CACHE_DIR=1 "$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/install-requirements.txt"
exec "$VENV_DIR/bin/python" "$SCRIPT_DIR/install.py" "$@"
