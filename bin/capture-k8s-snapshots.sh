#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${1:-${NAMESPACE:-media-stack}}"
OUT_DIR="${2:-$ROOT_DIR/docs/screenshots/cluster}"

mkdir -p "$OUT_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
prefix="$OUT_DIR/${ts}"

run_capture() {
  local name="$1"
  shift
  local path="${prefix}-${name}.txt"
  {
    echo "# $name"
    echo "# command: $*"
    echo "# captured_at_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo
    "$@" || true
  } >"$path"
  echo "[OK] $path"
}

echo "[INFO] Capturing Kubernetes terminal snapshots"
echo "[INFO] Namespace: $NAMESPACE"
echo "[INFO] Out dir: $OUT_DIR"

run_capture "kubectl-context" kubectl config current-context
run_capture "namespaces" kubectl get namespaces
run_capture "nodes" kubectl get nodes -o wide
run_capture "pods" kubectl -n "$NAMESPACE" get pods -o wide
run_capture "services" kubectl -n "$NAMESPACE" get svc
run_capture "ingress" kubectl -n "$NAMESPACE" get ingress
run_capture "pvc" kubectl -n "$NAMESPACE" get pvc
run_capture "deployments" kubectl -n "$NAMESPACE" get deploy
run_capture "statefulsets" kubectl -n "$NAMESPACE" get statefulset
run_capture "jobs" kubectl -n "$NAMESPACE" get jobs
run_capture "events" kubectl -n "$NAMESPACE" get events --sort-by=.lastTimestamp
run_capture "describe-ingress" kubectl -n "$NAMESPACE" describe ingress media-stack-ingress

echo "[OK] Kubernetes snapshot capture complete."
