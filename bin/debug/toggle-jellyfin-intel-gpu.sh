#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
ACTION="${1:-enable}"

usage() {
  cat <<'EOF'
Usage:
  bin/toggle-jellyfin-intel-gpu.sh [enable|disable]

Description:
  Opt-in helper for Intel /dev/dri hardware acceleration on Jellyfin.
  The default stack is storage-portable and hostPath-free.
  Use this only on nodes that expose /dev/dri.

Environment:
  NAMESPACE  (default: media-stack)
EOF
}

if [[ "$ACTION" == "-h" || "$ACTION" == "--help" ]]; then
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

case "$ACTION" in
  enable)
    "${KUBECTL[@]}" -n "$NAMESPACE" set volume deploy/jellyfin --add \
      --name=dri \
      --type=hostPath \
      --hostpath=/dev/dri \
      --mount-path=/dev/dri
    "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deploy/jellyfin --timeout=5m
    echo "[OK] Enabled Jellyfin Intel GPU mount (/dev/dri) in namespace '$NAMESPACE'."
    ;;
  disable)
    "${KUBECTL[@]}" -n "$NAMESPACE" set volume deploy/jellyfin --remove --name=dri
    "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deploy/jellyfin --timeout=5m
    echo "[OK] Disabled Jellyfin Intel GPU mount (/dev/dri) in namespace '$NAMESPACE'."
    ;;
  *)
    echo "[ERR] Unknown action '$ACTION'. Use enable or disable." >&2
    usage
    exit 1
    ;;
esac
