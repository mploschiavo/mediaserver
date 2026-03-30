#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
INTERVAL="${INTERVAL:-10}"
EVENT_LINES="${EVENT_LINES:-15}"
JOB_LOG_LINES="${JOB_LOG_LINES:-20}"
ONCE=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/watch-install.sh [--namespace NS] [--interval SEC] [--event-lines N] [--job-log-lines N] [--once]

Description:
  Live install/bootstrap watcher for media-stack.
  Shows pod/deployment health, recent warning events, and bootstrap job signal.

Options:
  --namespace NS    Kubernetes namespace (default: media-stack)
  --interval SEC    Refresh interval in seconds (default: 10)
  --event-lines N   Warning events to show each refresh (default: 15)
  --job-log-lines N Bootstrap job log tail size (default: 20)
  --once            Run one snapshot and exit
  -h, --help        Show this help
USAGE
}

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
info() { echo "[$(ts)] [INFO] $*"; }
warn() { echo "[$(ts)] [WARN] $*" >&2; }
err() { echo "[$(ts)] [ERR] $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      NAMESPACE="${2:-}"
      shift 2
      ;;
    --interval)
      INTERVAL="${2:-}"
      shift 2
      ;;
    --event-lines)
      EVENT_LINES="${2:-}"
      shift 2
      ;;
    --job-log-lines)
      JOB_LOG_LINES="${2:-}"
      shift 2
      ;;
    --once)
      ONCE=1
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

[[ "$INTERVAL" =~ ^[0-9]+$ ]] || err "--interval must be an integer"
[[ "$EVENT_LINES" =~ ^[0-9]+$ ]] || err "--event-lines must be an integer"
[[ "$JOB_LOG_LINES" =~ ^[0-9]+$ ]] || err "--job-log-lines must be an integer"

if ! "${KUBECTL[@]}" get namespace "$NAMESPACE" >/dev/null 2>&1; then
  err "Namespace '$NAMESPACE' not found."
fi

snapshot() {
  local pod_rows not_ready crashloop deploy_rows not_ready_deploys

  echo
  echo "=================================================================="
  info "Install Watch Snapshot"
  info "Namespace: $NAMESPACE | Refresh: ${INTERVAL}s"

  info "Pod status"
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods

  pod_rows="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods --no-headers 2>/dev/null || true)"
  if [[ -n "$pod_rows" ]]; then
    not_ready="$(printf '%s\n' "$pod_rows" | awk '{split($2,a,"/"); if (a[1] != a[2]) c++} END {print c+0}')"
    crashloop="$(printf '%s\n' "$pod_rows" | awk '$3 ~ /CrashLoopBackOff|Error|ImagePullBackOff|RunContainerError/ {c++} END {print c+0}')"
    info "Pod readiness summary: not_ready=${not_ready}, unhealthy=${crashloop}"
  else
    warn "No pods found in namespace $NAMESPACE"
  fi

  info "Deployment rollout status"
  "${KUBECTL[@]}" -n "$NAMESPACE" get deploy
  deploy_rows="$("${KUBECTL[@]}" -n "$NAMESPACE" get deploy --no-headers 2>/dev/null || true)"
  if [[ -n "$deploy_rows" ]]; then
    not_ready_deploys="$(printf '%s\n' "$deploy_rows" | awk '{if ($2 != $3) c++} END {print c+0}')"
    info "Deployment readiness summary: pending=${not_ready_deploys}"
  fi

  info "Recent warning events"
  "${KUBECTL[@]}" -n "$NAMESPACE" get events --sort-by=.lastTimestamp 2>/dev/null \
    | awk 'NR==1 || /Warning|Failed|BackOff|Unhealthy|Error/' \
    | tail -n "$EVENT_LINES" || true

  info "Bootstrap job status"
  "${KUBECTL[@]}" -n "$NAMESPACE" get jobs --sort-by=.metadata.creationTimestamp 2>/dev/null | tail -n 5 || true

  bootstrap_pod="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=media-stack-bootstrap -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [[ -n "$bootstrap_pod" ]]; then
    info "Tail bootstrap pod logs: $bootstrap_pod"
    "${KUBECTL[@]}" -n "$NAMESPACE" logs "$bootstrap_pod" --tail="$JOB_LOG_LINES" 2>/dev/null || true
  fi
}

if [[ "$ONCE" == "1" ]]; then
  snapshot
  exit 0
fi

info "Starting watcher; press Ctrl+C to stop."
while true; do
  snapshot
  sleep "$INTERVAL"
done
