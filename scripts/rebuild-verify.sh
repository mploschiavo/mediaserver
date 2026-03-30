#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_IP="${1:-${NODE_IP:-}}"
NAMESPACE="${2:-${NAMESPACE:-media-stack}}"
PROFILE="${3:-${PROFILE:-full}}"
INGRESS_DOMAIN="${INGRESS_DOMAIN:-local}"
RUN_PLAYWRIGHT="${RUN_PLAYWRIGHT:-0}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/rebuild-verify.sh <NODE_IP> [NAMESPACE] [PROFILE]

Description:
  End-to-end deterministic rebuild runner:
  1) install/deploy/bootstrap
  2) flow verification
  3) ingress smoke tests
  4) optional Playwright smoke
  5) final stack status snapshot

Environment:
  INGRESS_DOMAIN   ingress domain suffix (default: local)
  RUN_PLAYWRIGHT   1 to run Playwright ingress tests (default: 0)
USAGE
}

if [[ "$NODE_IP" == "-h" || "$NODE_IP" == "--help" || -z "$NODE_IP" ]]; then
  usage
  exit 0
fi

case "$PROFILE" in
  minimal|full|public-demo|power-user) ;;
  *)
    echo "[ERR] Unsupported profile '$PROFILE'. Use minimal|full|public-demo|power-user." >&2
    exit 1
    ;;
esac

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log() { echo "[$(ts)] [INFO] $*"; }

log "Starting rebuild and verification"
log "Node IP: $NODE_IP"
log "Namespace: $NAMESPACE"
log "Profile: $PROFILE"
log "Ingress domain: $INGRESS_DOMAIN"

log "Phase 1/5: install and bootstrap"
bash "$ROOT_DIR/scripts/install.sh" \
  --profile "$PROFILE" \
  --namespace "$NAMESPACE" \
  --ingress-domain "$INGRESS_DOMAIN" \
  --node-ip "$NODE_IP"

log "Phase 2/5: verify end-to-end flow"
bash "$ROOT_DIR/scripts/verify-flow.sh" "$NAMESPACE"

log "Phase 3/5: ingress smoke test"
bash "$ROOT_DIR/scripts/microk8s-smoke-test.sh" "$NODE_IP" "$NAMESPACE"

if [[ "$RUN_PLAYWRIGHT" == "1" ]]; then
  log "Phase 4/5: Playwright ingress smoke"
  bash "$ROOT_DIR/scripts/run-playwright-smoke.sh" "$NODE_IP" "$NAMESPACE"
else
  log "Phase 4/5: Playwright ingress smoke skipped (RUN_PLAYWRIGHT=0)"
fi

log "Phase 5/5: final status snapshot"
NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/stack-status.sh"

echo
echo "[OK] Rebuild + verification complete for namespace '$NAMESPACE'."
echo "[INFO] Render hosts entries if needed:"
echo "  bash scripts/render-hosts-example.sh $NODE_IP $NAMESPACE"
