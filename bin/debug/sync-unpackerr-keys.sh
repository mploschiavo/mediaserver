#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAMESPACE="${NAMESPACE:-media-stack}"

exec "$SCRIPT_DIR/../lib/run-python-cli.sh" sync_unpackerr_keys_main.py --namespace "$NAMESPACE" "$@"
