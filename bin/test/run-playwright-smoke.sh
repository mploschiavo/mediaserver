#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NODE_IP="${1:-${STACK_NODE_IP:-}}"
NAMESPACE="${2:-${NAMESPACE:-media-stack}}"

if [[ -z "${NODE_IP}" ]]; then
  echo "[ERR] Missing NODE_IP. Usage: bin/run-playwright-smoke.sh <NODE_IP> [NAMESPACE]" >&2
  exit 1
fi

HOSTS_LINE="$(bash "$ROOT_DIR/bin/utils/render-hosts-example.sh" "$NODE_IP" "$NAMESPACE")"
HOSTS_CSV="$(echo "$HOSTS_LINE" | cut -d' ' -f2- | tr ' ' ',')"

echo "[INFO] Running Playwright ingress tests"
echo "[INFO] NODE_IP: $NODE_IP"
echo "[INFO] NAMESPACE: $NAMESPACE"
echo "[INFO] HOSTS: $HOSTS_CSV"

pushd "$ROOT_DIR/tests/e2e/playwright" >/dev/null
if [[ ! -d node_modules ]]; then
  if ! npm ci; then
    npm install
  fi
fi
STACK_NODE_IP="$NODE_IP" STACK_HOSTS="$HOSTS_CSV" \
  npx playwright test tests/ingress.spec.ts tests/ux-smoke.spec.ts
popd >/dev/null
