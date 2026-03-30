#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-${NAMESPACE:-media-stack}}"
TAIL_LINES="${TAIL_LINES:-120}"

usage() {
  cat <<'EOF'
Usage:
  scripts/bootstrap-debug.sh [NAMESPACE]

Description:
  Collects useful bootstrap diagnostics in one shot:
  - pod/deployment/job summary
  - recent namespace events
  - logs for bootstrap jobs
  - previous logs for restarted pods

Environment variables:
  NAMESPACE   (default: media-stack)
  TAIL_LINES  (default: 120)
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

echo "=== Namespace: $NAMESPACE ==="
echo
echo "== Workload Summary =="
"${KUBECTL[@]}" -n "$NAMESPACE" get deploy,pods,job || true

echo
echo "== Recent Events =="
"${KUBECTL[@]}" -n "$NAMESPACE" get events --sort-by=.lastTimestamp | tail -n 200 || true

echo
echo "== Bootstrap Job Logs =="
"${KUBECTL[@]}" -n "$NAMESPACE" logs job/media-stack-bootstrap --tail="$TAIL_LINES" --timestamps 2>/dev/null || true
"${KUBECTL[@]}" -n "$NAMESPACE" logs job/media-stack-prowlarr-auto-indexers --tail="$TAIL_LINES" --timestamps 2>/dev/null || true

echo
echo "== Restarted Pod Previous Logs =="
mapfile -t restarted_pods < <(
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods --no-headers 2>/dev/null \
    | awk '$4 ~ /^[0-9]+$/ && $4 > 0 {print $1}'
)

if [[ "${#restarted_pods[@]}" -eq 0 ]]; then
  echo "No restarted pods found."
  exit 0
fi

for pod in "${restarted_pods[@]}"; do
  echo
  echo "--- Pod: $pod (describe) ---"
  "${KUBECTL[@]}" -n "$NAMESPACE" describe pod "$pod" | tail -n 120 || true
  echo "--- Pod: $pod (previous logs) ---"
  "${KUBECTL[@]}" -n "$NAMESPACE" logs "$pod" --previous --tail="$TAIL_LINES" 2>/dev/null || true
done
