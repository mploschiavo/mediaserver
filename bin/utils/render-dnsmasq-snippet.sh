#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-${2:-media-stack}}"
INGRESS_NAME="${INGRESS_NAME:-media-stack-ingress}"
NODE_IP="${1:-${NODE_IP:-}}"

usage() {
  cat <<'EOF'
Usage:
  bin/render-dnsmasq-snippet.sh <NODE_IP> [NAMESPACE]

Description:
  Prints dnsmasq/AdGuard Home compatible host mapping lines for all ingress hosts.
EOF
}

if [[ -z "$NODE_IP" || "$NODE_IP" == "-h" || "$NODE_IP" == "--help" ]]; then
  usage
  exit 0
fi

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  echo "[ERR] Neither microk8s nor kubectl is available in PATH." >&2
  exit 1
fi

mapfile -t hosts < <("${KUBECTL[@]}" -n "$NAMESPACE" get ingress "$INGRESS_NAME" -o jsonpath='{range .spec.rules[*]}{.host}{"\n"}{end}' 2>/dev/null)
if [[ "${#hosts[@]}" -eq 0 ]]; then
  echo "[ERR] No ingress hosts found on ${NAMESPACE}/${INGRESS_NAME}" >&2
  exit 1
fi

for host in "${hosts[@]}"; do
  [[ -z "$host" ]] && continue
  echo "address=/${host}/${NODE_IP}"
done
