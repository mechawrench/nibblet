#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
elif [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

cd "$ROOT_DIR"
exec "$PYTHON_BIN" tools/nibblet_bridge.py "$@"
