#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
USERNAME="${1:-}"
PASSWORD="${2:-}"
DEFAULT_STACK_ADMIN_USER="${DEFAULT_STACK_ADMIN_USER:-admin}"
DEFAULT_STACK_ADMIN_PASS="${DEFAULT_STACK_ADMIN_PASS:-media-stack-admin}"
DEFAULT_QBIT_USER="${DEFAULT_QBIT_USER:-$DEFAULT_STACK_ADMIN_USER}"
DEFAULT_QBIT_PASS="${DEFAULT_QBIT_PASS:-$DEFAULT_STACK_ADMIN_PASS}"
SYNC_STACK_ADMIN="${SYNC_STACK_ADMIN:-1}"

usage() {
  cat <<'EOF'
Usage:
  scripts/set-qbit-secret.sh [USERNAME] [PASSWORD]

Description:
  Sets or updates qBittorrent credentials in media-stack-secrets:
  - QBITTORRENT_USERNAME
  - QBITTORRENT_PASSWORD
  If USERNAME/PASSWORD are omitted, defaults are used.

Environment variables:
  NAMESPACE          (default: media-stack)
  DEFAULT_STACK_ADMIN_USER (default: admin)
  DEFAULT_STACK_ADMIN_PASS (default: media-stack-admin)
  DEFAULT_QBIT_USER  (default: DEFAULT_STACK_ADMIN_USER)
  DEFAULT_QBIT_PASS  (default: DEFAULT_STACK_ADMIN_PASS)
  SYNC_STACK_ADMIN   (default: 1; update STACK_ADMIN_* to match qB credentials)
EOF
}

if [[ "$USERNAME" == "-h" || "$USERNAME" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "$USERNAME" && -z "$PASSWORD" ]]; then
  USERNAME="$DEFAULT_QBIT_USER"
  PASSWORD="$DEFAULT_QBIT_PASS"
  echo "[INFO] Using default qB credentials from env defaults."
elif [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
  echo "[ERR] Provide both USERNAME and PASSWORD, or provide neither to use defaults." >&2
  exit 1
fi

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  echo "[ERR] Neither microk8s nor kubectl is available in PATH." >&2
  exit 1
fi

if ! "${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets >/dev/null 2>&1; then
  cat <<EOF | "${KUBECTL[@]}" apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: media-stack-secrets
  namespace: $NAMESPACE
type: Opaque
stringData:
  QBITTORRENT_USERNAME: "$USERNAME"
  QBITTORRENT_PASSWORD: "$PASSWORD"
  STACK_ADMIN_USERNAME: "$DEFAULT_STACK_ADMIN_USER"
  STACK_ADMIN_PASSWORD: "$DEFAULT_STACK_ADMIN_PASS"
  JELLYFIN_API_KEY: ""
  JELLYFIN_USER_ID: ""
  UNPACKERR_SONARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_RADARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_LIDARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_READARR_API_KEY: "replace-after-first-boot"
EOF
  echo "[OK] Created $NAMESPACE/media-stack-secrets with qBittorrent + stack admin defaults."
  exit 0
fi

if [[ "$SYNC_STACK_ADMIN" == "1" ]]; then
  "${KUBECTL[@]}" -n "$NAMESPACE" patch secret media-stack-secrets \
    --type merge \
    -p "{\"stringData\":{\"QBITTORRENT_USERNAME\":\"$USERNAME\",\"QBITTORRENT_PASSWORD\":\"$PASSWORD\",\"STACK_ADMIN_USERNAME\":\"$USERNAME\",\"STACK_ADMIN_PASSWORD\":\"$PASSWORD\"}}"
else
  "${KUBECTL[@]}" -n "$NAMESPACE" patch secret media-stack-secrets \
    --type merge \
    -p "{\"stringData\":{\"QBITTORRENT_USERNAME\":\"$USERNAME\",\"QBITTORRENT_PASSWORD\":\"$PASSWORD\"}}"
fi

echo "[OK] Updated qBittorrent credentials in $NAMESPACE/media-stack-secrets."
