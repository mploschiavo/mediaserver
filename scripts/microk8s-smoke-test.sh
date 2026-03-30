#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
INGRESS_NAME="${INGRESS_NAME:-media-stack-ingress}"
NODE_IP="${1:-}"
if [[ $# -ge 2 ]]; then
  NAMESPACE="${2:-$NAMESPACE}"
fi
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${NODE_IP:-}" == "-h" || "${NODE_IP:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  scripts/microk8s-smoke-test.sh [NODE_IP] [NAMESPACE]

Description:
  Runs a quick LAN smoke test for media-stack ingress routing.
  - Verifies pods/services/ingress
  - Prints ingress class
  - Tests each ingress host with curl Host headers against NODE_IP
  - Prints hosts-file helper line

Environment variables:
  NAMESPACE    (default: media-stack)
  INGRESS_NAME (default: media-stack-ingress)
EOF
  exit 0
fi

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

err() {
  printf '[ERR] %s\n' "$*" >&2
  exit 1
}

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  err "Neither 'microk8s' nor 'kubectl' is available in PATH."
fi

if ! command -v curl >/dev/null 2>&1; then
  err "'curl' is required for smoke tests."
fi

if [[ -z "$NODE_IP" ]]; then
  NODE_IP="$(hostname -I | awk '{print $1}')"
fi

if [[ -z "$NODE_IP" ]]; then
  err "Unable to detect node IP. Pass it explicitly: scripts/microk8s-smoke-test.sh <NODE_IP>"
fi

printf 'Using kubectl command: %s\n' "${KUBECTL[*]}"
printf 'Namespace: %s\nIngress: %s\nNode IP: %s\n\n' "$NAMESPACE" "$INGRESS_NAME" "$NODE_IP"

"${KUBECTL[@]}" -n "$NAMESPACE" get pods
"${KUBECTL[@]}" -n "$NAMESPACE" get svc,ingress
"${KUBECTL[@]}" get ingressclass

ingress_class="$("${KUBECTL[@]}" -n "$NAMESPACE" get ingress "$INGRESS_NAME" -o jsonpath='{.spec.ingressClassName}' 2>/dev/null || true)"
class_valid=1
mapfile -t INGRESS_CLASSES < <("${KUBECTL[@]}" get ingressclass -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)

if [[ -n "$ingress_class" ]]; then
  found_class=0
  for cls in "${INGRESS_CLASSES[@]}"; do
    if [[ "$cls" == "$ingress_class" ]]; then
      found_class=1
      break
    fi
  done

  if [[ "$found_class" -eq 1 ]]; then
    ok "Ingress class on $INGRESS_NAME: $ingress_class"
  else
    class_valid=0
    warn "Ingress class on $INGRESS_NAME is '$ingress_class', but available classes are: ${INGRESS_CLASSES[*]:-(none)}"
    warn "Patch example: bash \"$SCRIPT_DIR/microk8s-patch-ingress-class.sh\" public"
  fi
else
  class_valid=0
  warn "Ingress class is empty on $INGRESS_NAME"
fi

# Pull host/service pairs directly from ingress rules so tests always match manifests.
mapfile -t INGRESS_RULES < <("${KUBECTL[@]}" -n "$NAMESPACE" get ingress "$INGRESS_NAME" -o jsonpath='{range .spec.rules[*]}{.host}{"|"}{.http.paths[0].backend.service.name}{"\n"}{end}')

if [[ "${#INGRESS_RULES[@]}" -eq 0 ]]; then
  err "No ingress hosts found on $INGRESS_NAME"
fi

printf '\nTesting ingress routes from this node (Host header -> http://%s/)\n' "$NODE_IP"

failures=0
for rule in "${INGRESS_RULES[@]}"; do
  host="${rule%%|*}"
  svc="${rule##*|}"
  [[ -z "$host" ]] && continue

  if [[ -n "$svc" ]] && ! "${KUBECTL[@]}" -n "$NAMESPACE" get svc "$svc" >/dev/null 2>&1; then
    warn "$host -> skipped (backend service '$svc' not installed)"
    continue
  fi

  code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Host: $host" "http://$NODE_IP/" || true)"
  case "$code" in
    200|301|302|303|307|308|401|403)
      ok "$host -> HTTP $code"
      ;;
    *)
      warn "$host -> HTTP ${code:-000}"
      failures=$((failures + 1))
      ;;
  esac
done

printf '\nHosts file helper:\n'
INGRESS_NAME="$INGRESS_NAME" bash "$SCRIPT_DIR/render-hosts-example.sh" "$NODE_IP" "$NAMESPACE"

if [[ "$failures" -gt 0 ]]; then
  if [[ "$class_valid" -eq 0 ]]; then
    err "Smoke test failed and ingress class appears invalid/missing."
  fi
  err "Smoke test completed with $failures failing route(s)."
fi

ok "Smoke test passed for all ingress hosts."
