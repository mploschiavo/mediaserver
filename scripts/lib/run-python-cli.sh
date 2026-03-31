#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 1 ]]; then
  echo "[ERR] Usage: scripts/lib/run-python-cli.sh <cli-script-name.py> [args...]" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CLI_SCRIPT="$1"
shift || true
CLI_PATH="$SCRIPT_DIR/cli/$CLI_SCRIPT"

if [[ ! -f "$CLI_PATH" ]]; then
  echo "[ERR] CLI script not found: $CLI_PATH" >&2
  exit 2
fi

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
else
  export PYTHONPATH="$SCRIPT_DIR"
fi

exec "$PYTHON_BIN" "$CLI_PATH" "$@"
