#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_IP="${1:-${STACK_NODE_IP:-}}"
NAMESPACE="${2:-${NAMESPACE:-media-stack}}"
OUT_DIR="${3:-${STACK_SCREENSHOT_DIR:-$ROOT_DIR/docs/screenshots/apps}}"

if [[ -z "${NODE_IP}" ]]; then
  echo "[ERR] Missing NODE_IP. Usage: scripts/run-playwright-screenshots.sh <NODE_IP> [NAMESPACE] [OUT_DIR]" >&2
  exit 1
fi

HOSTS_LINE="$(bash "$ROOT_DIR/scripts/render-hosts-example.sh" "$NODE_IP" "$NAMESPACE")"
HOSTS_CSV="$(echo "$HOSTS_LINE" | cut -d' ' -f2- | tr ' ' ',')"

echo "[INFO] Capturing Playwright UI screenshots"
echo "[INFO] NODE_IP: $NODE_IP"
echo "[INFO] NAMESPACE: $NAMESPACE"
echo "[INFO] OUT_DIR: $OUT_DIR"
echo "[INFO] HOSTS: $HOSTS_CSV"

mkdir -p "$OUT_DIR"

pushd "$ROOT_DIR/tests/e2e/playwright" >/dev/null
if [[ ! -d node_modules ]]; then
  if ! npm ci; then
    npm install
  fi
fi

if ! STACK_NODE_IP="$NODE_IP" STACK_HOSTS="$HOSTS_CSV" STACK_SCREENSHOT_DIR="$OUT_DIR" \
  npx playwright test tests/screenshot-capture.spec.ts --reporter=list --workers=1; then
  STACK_NODE_IP="$NODE_IP" STACK_HOSTS="$HOSTS_CSV" STACK_SCREENSHOT_DIR="$OUT_DIR" \
    npx playwright install chromium >/dev/null 2>&1 || true
  STACK_NODE_IP="$NODE_IP" STACK_HOSTS="$HOSTS_CSV" STACK_SCREENSHOT_DIR="$OUT_DIR" \
    npx playwright test tests/screenshot-capture.spec.ts --reporter=list --workers=1
fi
popd >/dev/null

echo "[OK] Screenshot capture complete."
ls -1 "$OUT_DIR"/*.png 2>/dev/null || true
