#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || $# -lt 2 ]]; then
  cat <<'EOF'
Usage:
  scripts/with-env.sh <ENV_FILE> <COMMAND> [ARGS...]

Description:
  Sources ENV_FILE with exported variables, applies safe default
  DELETE_NAMESPACE=0 when unset, then executes COMMAND.

Examples:
  bash scripts/with-env.sh examples/environments/media-dev.env.example \
    bash scripts/install.sh
  bash scripts/with-env.sh examples/environments/media-dev.env.example \
    bash scripts/rebuild-and-bootstrap.sh
EOF
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    exit 0
  fi
  exit 2
fi

ENV_FILE="$1"
shift

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[ERR] Env file not found: $ENV_FILE" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

export DELETE_NAMESPACE="${DELETE_NAMESPACE:-0}"

echo "[INFO] Loaded env: $ENV_FILE" >&2
echo "[INFO] Namespace=${NAMESPACE:-<unset>} IngressDomain=${INGRESS_DOMAIN:-<unset>} DELETE_NAMESPACE=${DELETE_NAMESPACE}" >&2

exec "$@"
