#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $# -lt 1 ]]; then
  echo "[ERR] Usage: bin/lib/run-python-cli.sh <cli-script-name.py> [args...]" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CLI_SCRIPT="$1"
shift || true
CLI_PATH="$ROOT_DIR/src/media_stack/cli/commands/$CLI_SCRIPT"
CLI_MODULE=""

if [[ ! -f "$CLI_PATH" ]]; then
  APP_CLI_ROOT="$ROOT_DIR/src/media_stack/services/apps"
  APP_MATCHES=()
  if [[ -d "$APP_CLI_ROOT" ]]; then
    mapfile -t APP_MATCHES < <(
      find "$APP_CLI_ROOT" -type f -path "*/cli/$CLI_SCRIPT" | sort
    )
  fi
  if [[ "${#APP_MATCHES[@]}" -eq 1 ]]; then
    CLI_PATH="${APP_MATCHES[0]}"
    CLI_MODULE="${CLI_PATH#$ROOT_DIR/src/}"
    CLI_MODULE="${CLI_MODULE%.py}"
    CLI_MODULE="${CLI_MODULE//\//.}"
  elif [[ "${#APP_MATCHES[@]}" -gt 1 ]]; then
    echo "[ERR] Ambiguous CLI script '$CLI_SCRIPT' found in multiple app paths:" >&2
    printf '  - %s\n' "${APP_MATCHES[@]}" >&2
    exit 2
  else
    echo "[ERR] CLI script not found: $ROOT_DIR/src/media_stack/cli/commands/$CLI_SCRIPT" >&2
    exit 2
  fi
fi

if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="$ROOT_DIR/src:$ROOT_DIR:$PYTHONPATH"
else
  export PYTHONPATH="$ROOT_DIR/src:$ROOT_DIR"
fi

if [[ -n "$CLI_MODULE" ]]; then
  exec "$PYTHON_BIN" -m "$CLI_MODULE" "$@"
fi

exec "$PYTHON_BIN" "$CLI_PATH" "$@"
