#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${PROFILE:-full}"
NODE_IP="${NODE_IP:-}"
NAMESPACE="${NAMESPACE:-media-stack}"
PREPARE_HOST_ROOT="${PREPARE_HOST_ROOT:-/srv/media-stack}"
STORAGE_MODE="${STORAGE_MODE:-dynamic-pvc}"
PVC_STORAGE_CLASS="${PVC_STORAGE_CLASS:-}"
INGRESS_DOMAIN="${INGRESS_DOMAIN:-local}"
ENABLE_TLS="${ENABLE_TLS:-0}"
ENABLE_SECRETS_GEN="${ENABLE_SECRETS_GEN:-1}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
RUN_START_EPOCH="$(date +%s)"
CURRENT_PHASE=""
CURRENT_PHASE_START=0
declare -a PHASE_NAMES=()
declare -a PHASE_RESULTS=()
declare -a PHASE_SECONDS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/install.sh [--profile PROFILE] [--namespace NS] [--storage-mode MODE] [--storage-class CLASS] [--ingress-domain DOMAIN] [--node-ip IP] [--enable-tls]

Description:
  One-command install wizard for media-stack:
  - preflight checks
  - host folder prep
  - secure secret generation
  - profile-based deploy + bootstrap
  - optional LAN TLS setup
  - final status summary

Options:
  --profile PROFILE   minimal|full|public-demo|power-user (default: full)
  --namespace NS      namespace to deploy into (default: media-stack)
  --storage-mode MODE dynamic-pvc|legacy-hostpath (default: dynamic-pvc)
  --storage-class C   optional PVC storageClassName override for all stack PVCs
  --ingress-domain D  ingress hostname suffix (default: local; e.g. dev.local)
  --node-ip IP        LAN IP for smoke tests and host helpers
  --enable-tls        generate/apply LAN TLS cert secret + ingress tls
  -h, --help          show this help

Environment variables:
  PROFILE             same as --profile
  NODE_IP             same as --node-ip
  ENABLE_TLS          1 to enable TLS helper
  ENABLE_SECRETS_GEN  1 to generate/apply secure secret (default: 1)
  NAMESPACE           (default: media-stack)
  STORAGE_MODE        dynamic-pvc|legacy-hostpath (default: dynamic-pvc)
  PVC_STORAGE_CLASS   optional PVC storageClassName override
  PREPARE_HOST_ROOT   (default: /srv/media-stack)
  INGRESS_DOMAIN      (default: local)
  ALERT_WEBHOOK_URL   optional webhook for status notifications
EOF
}

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
info() { echo "[$(ts)] [INFO] $*"; }
warn() { echo "[$(ts)] [WARN] $*" >&2; }
err() { echo "[$(ts)] [ERR] $*" >&2; exit 1; }

notify() {
  local status="$1"
  local message="$2"
  [[ -z "$ALERT_WEBHOOK_URL" ]] && return 0
  curl -fsS -X POST \
    -H "Content-Type: application/json" \
    --data "{\"status\":\"$status\",\"message\":\"$message\"}" \
    "$ALERT_WEBHOOK_URL" >/dev/null || true
}

phase_start() {
  CURRENT_PHASE="$1"
  CURRENT_PHASE_START="$(date +%s)"
  info "[PHASE] START: $CURRENT_PHASE"
}

phase_end() {
  local result="${1:-ok}"
  local now elapsed
  now="$(date +%s)"

  if [[ -n "$CURRENT_PHASE" && "$CURRENT_PHASE_START" -gt 0 ]]; then
    elapsed=$((now - CURRENT_PHASE_START))
    PHASE_NAMES+=("$CURRENT_PHASE")
    PHASE_RESULTS+=("$result")
    PHASE_SECONDS+=("$elapsed")

    case "$result" in
      ok) info "[PHASE] DONE: $CURRENT_PHASE (${elapsed}s)" ;;
      skipped) info "[PHASE] SKIP: $CURRENT_PHASE (${elapsed}s)" ;;
      *) warn "[PHASE] FAIL: $CURRENT_PHASE (${elapsed}s)" ;;
    esac
  fi

  CURRENT_PHASE=""
  CURRENT_PHASE_START=0
}

print_phase_summary() {
  local total i
  total=$(( $(date +%s) - RUN_START_EPOCH ))
  info "Phase Summary (total ${total}s)"
  if [[ "${#PHASE_NAMES[@]}" -eq 0 ]]; then
    info "  (no phases recorded)"
    return 0
  fi
  for i in "${!PHASE_NAMES[@]}"; do
    info "  ${PHASE_NAMES[$i]} => ${PHASE_RESULTS[$i]} (${PHASE_SECONDS[$i]}s)"
  done
}

on_error() {
  local code="$?"
  local line="$1"
  local cmd="$2"
  if [[ -n "$CURRENT_PHASE" ]]; then
    phase_end "failed"
  fi
  warn "Install failed at line ${line} while running: ${cmd}"
  print_phase_summary
  notify "error" "media-stack install failed (profile=$PROFILE, namespace=$NAMESPACE)"
  exit "$code"
}

trap 'on_error $LINENO "$BASH_COMMAND"' ERR

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    --node-ip)
      NODE_IP="${2:-}"
      shift 2
      ;;
    --namespace)
      NAMESPACE="${2:-}"
      shift 2
      ;;
    --storage-mode)
      STORAGE_MODE="${2:-}"
      shift 2
      ;;
    --storage-class)
      PVC_STORAGE_CLASS="${2:-}"
      shift 2
      ;;
    --ingress-domain)
      INGRESS_DOMAIN="${2:-}"
      shift 2
      ;;
    --enable-tls)
      ENABLE_TLS=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      ;;
  esac
done

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  err "Neither microk8s nor kubectl is available in PATH."
fi

for cmd in bash awk sed curl; do
  command -v "$cmd" >/dev/null 2>&1 || err "Missing required command: $cmd"
done

case "$PROFILE" in
  minimal|full|public-demo|power-user) ;;
  *) err "Unsupported profile '$PROFILE'. Use minimal|full|public-demo|power-user." ;;
esac
case "$STORAGE_MODE" in
  dynamic-pvc|legacy-hostpath) ;;
  *) err "Unsupported storage mode '$STORAGE_MODE'. Use dynamic-pvc|legacy-hostpath." ;;
esac
[[ -n "$NAMESPACE" ]] || err "Namespace cannot be empty."
INGRESS_DOMAIN="${INGRESS_DOMAIN#.}"
[[ -n "$INGRESS_DOMAIN" ]] || err "Ingress domain cannot be empty."
[[ -d "$ROOT_DIR/k8s/profiles/$PROFILE" ]] || err "Missing profile directory: k8s/profiles/$PROFILE"

if [[ -z "$NODE_IP" ]]; then
  NODE_IP="$(hostname -I | awk '{print $1}')"
fi
[[ -n "$NODE_IP" ]] || err "Could not detect node IP. Pass --node-ip."

info "Install start"
info "Profile: $PROFILE"
info "Namespace: $NAMESPACE"
info "Storage mode: $STORAGE_MODE"
if [[ -n "$PVC_STORAGE_CLASS" ]]; then
  info "PVC storage class override: $PVC_STORAGE_CLASS"
else
  info "PVC storage class override: <cluster default>"
fi
info "Ingress domain: $INGRESS_DOMAIN"
info "Node IP: $NODE_IP"
notify "info" "media-stack install started (profile=$PROFILE)"

phase_start "Preflight checks"
info "Preflight: checking ingress classes"
if ! "${KUBECTL[@]}" get ingressclass >/dev/null 2>&1; then
  warn "No ingress classes returned yet. Install may still succeed after ingress add-on is enabled."
fi
phase_end "ok"

phase_start "Prepare host directories"
if [[ "$STORAGE_MODE" == "legacy-hostpath" ]]; then
  info "Preparing host directories under $PREPARE_HOST_ROOT"
  bash "$ROOT_DIR/scripts/prepare-host.sh" "$PREPARE_HOST_ROOT"
  phase_end "ok"
else
  info "Skipping host directory prep (storage mode: dynamic-pvc)"
  phase_end "skipped"
fi

phase_start "Apply scale policy guardrails (dry-run)"
info "Applying scale policy guardrails"
NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/apply-scale-policy.sh" --dry-run >/dev/null || true
phase_end "ok"

phase_start "Deploy and bootstrap stack"
info "Deploying and bootstrapping stack"
PROFILE="$PROFILE" NAMESPACE="$NAMESPACE" NODE_IP="$NODE_IP" ALERT_WEBHOOK_URL="$ALERT_WEBHOOK_URL" \
  STORAGE_MODE="$STORAGE_MODE" \
  PVC_STORAGE_CLASS="$PVC_STORAGE_CLASS" \
  PREPARE_HOST_ROOT="$PREPARE_HOST_ROOT" \
  INGRESS_DOMAIN="$INGRESS_DOMAIN" \
  GENERATE_SECRETS_ON_REBUILD="$ENABLE_SECRETS_GEN" \
  bash "$ROOT_DIR/scripts/rebuild-and-bootstrap.sh" "$NODE_IP"
phase_end "ok"

if [[ "$ENABLE_TLS" == "1" ]]; then
  phase_start "Configure LAN TLS"
  info "Setting up LAN TLS certificates"
  NAMESPACE="$NAMESPACE" NODE_IP="$NODE_IP" bash "$ROOT_DIR/scripts/setup-lan-tls.sh"
  phase_end "ok"
else
  phase_start "Configure LAN TLS"
  phase_end "skipped"
fi

phase_start "Collect final stack status"
info "Collecting final status"
NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/stack-status.sh"
phase_end "ok"
print_phase_summary

echo
echo "[OK] Install complete."
echo "[INFO] Primary URLs:"
echo "  http://homepage.${INGRESS_DOMAIN}"
echo "  http://jellyfin.${INGRESS_DOMAIN}"
echo "  http://jellyseerr.${INGRESS_DOMAIN}"
echo "[INFO] Host entries helper:"
echo "  bash scripts/render-hosts-example.sh $NODE_IP $NAMESPACE"
echo "[INFO] Generated secrets file:"
echo "  $ROOT_DIR/secrets.generated.env"

notify "ok" "media-stack install succeeded (profile=$PROFILE)"
