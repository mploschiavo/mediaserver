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
CLI_MODULE=""

if [[ ! -f "$CLI_PATH" ]]; then
  APP_CLI_ROOT="$SCRIPT_DIR/bootstrap_services/apps"
  APP_MATCHES=()
  if [[ -d "$APP_CLI_ROOT" ]]; then
    mapfile -t APP_MATCHES < <(
      find "$APP_CLI_ROOT" -type f -path "*/cli/$CLI_SCRIPT" | sort
    )
  fi
  if [[ "${#APP_MATCHES[@]}" -eq 1 ]]; then
    CLI_PATH="${APP_MATCHES[0]}"
    CLI_MODULE="${CLI_PATH#$SCRIPT_DIR/}"
    CLI_MODULE="${CLI_MODULE%.py}"
    CLI_MODULE="${CLI_MODULE//\//.}"
  elif [[ "${#APP_MATCHES[@]}" -gt 1 ]]; then
    echo "[ERR] Ambiguous CLI script '$CLI_SCRIPT' found in multiple app paths:" >&2
    printf '  - %s\n' "${APP_MATCHES[@]}" >&2
    exit 2
  else
    echo "[ERR] CLI script not found: $SCRIPT_DIR/cli/$CLI_SCRIPT" >&2
    exit 2
  fi
fi

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$SCRIPT_DIR:$PYTHONPATH"
else
  export PYTHONPATH="$SCRIPT_DIR"
fi

if [[ -n "$CLI_MODULE" ]]; then
  exec "$PYTHON_BIN" -m "$CLI_MODULE" "$@"
fi

exec "$PYTHON_BIN" "$CLI_PATH" "$@"
