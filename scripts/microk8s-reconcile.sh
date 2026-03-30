#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-20m}"
INCLUDE_OPTIONAL=0

usage() {
  cat <<'EOF'
Usage:
  scripts/microk8s-reconcile.sh [--include-optional]

Description:
  Reconciles media-stack manifests on MicroK8s/Kubernetes, then restarts all
  Deployments in the namespace so pods pick up current templates.

Environment variables:
  NAMESPACE     (default: media-stack)
  WAIT_TIMEOUT  (default: 20m)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-optional)
      INCLUDE_OPTIONAL=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '[ERR] Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  printf '[ERR] Neither microk8s nor kubectl is available in PATH.\n' >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

printf '[INFO] Applying core manifests from %s/k8s\n' "$ROOT_DIR"
"${KUBECTL[@]}" apply -k "$ROOT_DIR/k8s"

if [[ "$INCLUDE_OPTIONAL" -eq 1 ]]; then
  printf '[INFO] Applying optional manifests from %s/k8s/optional.yaml\n' "$ROOT_DIR"
  "${KUBECTL[@]}" apply -f "$ROOT_DIR/k8s/optional.yaml"
else
  existing_optional="$("${KUBECTL[@]}" -n "$NAMESPACE" get deploy -o name 2>/dev/null | grep -E 'deploy/(homepage|plex|tautulli|sabnzbd|flaresolverr)' || true)"
  if [[ -n "$existing_optional" ]]; then
    printf '[INFO] Existing optional deployments detected; applying optional.yaml to keep templates in sync.\n'
    "${KUBECTL[@]}" apply -f "$ROOT_DIR/k8s/optional.yaml"
  fi
fi

if "${KUBECTL[@]}" -n "$NAMESPACE" get deploy unpackerr >/dev/null 2>&1; then
  printf '[INFO] Applying unpackerr manifest (replicas default to 0 in repo).\n'
  "${KUBECTL[@]}" apply -f "$ROOT_DIR/k8s/unpackerr.yaml"
fi

printf '[INFO] Restarting all deployments in namespace %s\n' "$NAMESPACE"
"${KUBECTL[@]}" -n "$NAMESPACE" rollout restart deploy --all

mapfile -t DEPLOYS < <("${KUBECTL[@]}" -n "$NAMESPACE" get deploy -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')

failed=0
for deploy in "${DEPLOYS[@]}"; do
  [[ -z "$deploy" ]] && continue
  printf '[INFO] Waiting for deploy/%s\n' "$deploy"
  if ! "${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/$deploy" --timeout="$WAIT_TIMEOUT"; then
    printf '[WARN] deploy/%s did not become ready in %s\n' "$deploy" "$WAIT_TIMEOUT" >&2
    failed=$((failed + 1))
  fi
done

printf '\n[INFO] Current pod state:\n'
"${KUBECTL[@]}" -n "$NAMESPACE" get pods

if [[ "$failed" -gt 0 ]]; then
  printf '\n[WARN] %d deployment(s) still not ready.\n' "$failed" >&2
  printf '[WARN] Inspect with:\n' >&2
  printf '  %s -n %s get events --sort-by=.lastTimestamp | tail -n 200\n' "${KUBECTL[*]}" "$NAMESPACE" >&2
  printf '  %s -n %s logs deploy/<name> --tail=200\n' "${KUBECTL[*]}" "$NAMESPACE" >&2
  exit 1
fi

printf '\n[OK] Reconcile complete.\n'
