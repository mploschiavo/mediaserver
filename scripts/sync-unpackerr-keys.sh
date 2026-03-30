#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

NAMESPACE="${NAMESPACE:-media-stack}"

exec "$PYTHON_BIN" "$SCRIPT_DIR/sync_unpackerr_keys.py" --namespace "$NAMESPACE" "$@"
