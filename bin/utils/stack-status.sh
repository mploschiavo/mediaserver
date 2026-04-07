#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
EVENT_LINES="${EVENT_LINES:-80}"

usage() {
  cat <<'EOF'
Usage:
  bin/stack-status.sh

Description:
  Prints an operational status snapshot for media-stack:
  - deployment and pod readiness
  - restart hotspots
  - ingress/service summary
  - recent events

Environment variables:
  NAMESPACE    (default: media-stack)
  EVENT_LINES  (default: 80)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
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

echo "== Deployments =="
"${KUBECTL[@]}" -n "$NAMESPACE" get deploy -o wide
echo
echo "== Pods =="
"${KUBECTL[@]}" -n "$NAMESPACE" get pods -o wide
echo
echo "== Restart Hotspots =="
"${KUBECTL[@]}" -n "$NAMESPACE" get pods --no-headers \
  -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount,PHASE:.status.phase \
  | sort -k3 -nr | head -n 20
echo
echo "== Services and Ingress =="
"${KUBECTL[@]}" -n "$NAMESPACE" get svc,ingress
echo
echo "== Recent Events =="
"${KUBECTL[@]}" -n "$NAMESPACE" get events --sort-by=.lastTimestamp | tail -n "$EVENT_LINES"
