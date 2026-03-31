#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
USERNAME="${1:-}"
PASSWORD="${2:-}"
DEFAULT_STACK_ADMIN_USER="${DEFAULT_STACK_ADMIN_USER:-admin}"
DEFAULT_STACK_ADMIN_PASS="${DEFAULT_STACK_ADMIN_PASS:-media-stack-admin}"
WRITE_LEGACY_QBIT_KEYS="${WRITE_LEGACY_QBIT_KEYS:-0}"

usage() {
  cat <<'EOF'
Usage:
  scripts/set-qbit-secret.sh [USERNAME] [PASSWORD]

Description:
  Sets or updates stack admin credentials in media-stack-secrets.
  qBittorrent uses STACK_ADMIN_USERNAME / STACK_ADMIN_PASSWORD by default.
  If USERNAME/PASSWORD are omitted, defaults are used.

Environment variables:
  NAMESPACE          (default: media-stack)
  DEFAULT_STACK_ADMIN_USER (default: admin)
  DEFAULT_STACK_ADMIN_PASS (default: media-stack-admin)
  WRITE_LEGACY_QBIT_KEYS (default: 0; if 1 also writes legacy QBITTORRENT_* keys)
EOF
}

if [[ "$USERNAME" == "-h" || "$USERNAME" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "$USERNAME" && -z "$PASSWORD" ]]; then
  USERNAME="$DEFAULT_STACK_ADMIN_USER"
  PASSWORD="$DEFAULT_STACK_ADMIN_PASS"
  echo "[INFO] Using default stack admin credentials from env defaults."
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
  STACK_ADMIN_USERNAME: "$USERNAME"
  STACK_ADMIN_PASSWORD: "$PASSWORD"
  JELLYFIN_API_KEY: ""
  JELLYFIN_USER_ID: ""
  UNPACKERR_SONARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_RADARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_LIDARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_READARR_API_KEY: "replace-after-first-boot"
EOF
  if [[ "$WRITE_LEGACY_QBIT_KEYS" == "1" ]]; then
    "${KUBECTL[@]}" -n "$NAMESPACE" patch secret media-stack-secrets \
      --type merge \
      -p "{\"stringData\":{\"QBITTORRENT_USERNAME\":\"$USERNAME\",\"QBITTORRENT_PASSWORD\":\"$PASSWORD\"}}" >/dev/null
  fi
  echo "[OK] Created $NAMESPACE/media-stack-secrets with stack admin credentials."
  exit 0
fi

if [[ "$WRITE_LEGACY_QBIT_KEYS" == "1" ]]; then
  "${KUBECTL[@]}" -n "$NAMESPACE" patch secret media-stack-secrets \
    --type merge \
    -p "{\"stringData\":{\"STACK_ADMIN_USERNAME\":\"$USERNAME\",\"STACK_ADMIN_PASSWORD\":\"$PASSWORD\",\"QBITTORRENT_USERNAME\":\"$USERNAME\",\"QBITTORRENT_PASSWORD\":\"$PASSWORD\"}}" >/dev/null
else
  "${KUBECTL[@]}" -n "$NAMESPACE" patch secret media-stack-secrets \
    --type merge \
    -p "{\"stringData\":{\"STACK_ADMIN_USERNAME\":\"$USERNAME\",\"STACK_ADMIN_PASSWORD\":\"$PASSWORD\"}}" >/dev/null
fi

echo "[OK] Updated stack admin credentials in $NAMESPACE/media-stack-secrets."
