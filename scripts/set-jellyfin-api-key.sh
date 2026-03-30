#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
API_KEY="${1:-}"

usage() {
  cat <<'EOF'
Usage:
  scripts/set-jellyfin-api-key.sh <JELLYFIN_API_KEY>

Description:
  Sets or updates Jellyfin API key in media-stack-secrets:
  - JELLYFIN_API_KEY

Environment variables:
  NAMESPACE   (default: media-stack)
EOF
}

if [[ "$API_KEY" == "-h" || "$API_KEY" == "--help" || -z "$API_KEY" ]]; then
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

if ! "${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets >/dev/null 2>&1; then
  cat <<EOF | "${KUBECTL[@]}" apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: media-stack-secrets
  namespace: $NAMESPACE
type: Opaque
stringData:
  JELLYFIN_API_KEY: "$API_KEY"
  JELLYFIN_USER_ID: ""
EOF
  echo "[OK] Created $NAMESPACE/media-stack-secrets with Jellyfin API key."
  exit 0
fi

"${KUBECTL[@]}" -n "$NAMESPACE" patch secret media-stack-secrets \
  --type merge \
  -p "{\"stringData\":{\"JELLYFIN_API_KEY\":\"$API_KEY\"}}"

echo "[OK] Updated Jellyfin API key in $NAMESPACE/media-stack-secrets."
