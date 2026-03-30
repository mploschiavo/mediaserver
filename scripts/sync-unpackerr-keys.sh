#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"

usage() {
  cat <<'EOF'
Usage:
  scripts/sync-unpackerr-keys.sh

Description:
  Reads Sonarr/Radarr/Lidarr/Readarr/Prowlarr API keys from running pods and updates
  media-stack-secrets for both Unpackerr and bootstrap env fallback.

Environment variables:
  NAMESPACE   (default: media-stack)
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

get_key() {
  local app="$1"
  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$app" -- sh -c \
    "sed -n 's:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p' /config/config.xml | head -n1"
}

SONARR_KEY="$(get_key sonarr)"
RADARR_KEY="$(get_key radarr)"
LIDARR_KEY="$(get_key lidarr)"
READARR_KEY="$(get_key readarr)"
PROWLARR_KEY="$(get_key prowlarr)"

if [[ -z "$SONARR_KEY" || -z "$RADARR_KEY" || -z "$LIDARR_KEY" || -z "$READARR_KEY" || -z "$PROWLARR_KEY" ]]; then
  echo "[ERR] One or more API keys were empty. Ensure Arr apps are healthy first." >&2
  exit 1
fi

cat <<EOF | "${KUBECTL[@]}" apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: media-stack-secrets
  namespace: $NAMESPACE
type: Opaque
stringData:
  SONARR_API_KEY: "$SONARR_KEY"
  RADARR_API_KEY: "$RADARR_KEY"
  LIDARR_API_KEY: "$LIDARR_KEY"
  READARR_API_KEY: "$READARR_KEY"
  PROWLARR_API_KEY: "$PROWLARR_KEY"
  UNPACKERR_SONARR_API_KEY: "$SONARR_KEY"
  UNPACKERR_RADARR_API_KEY: "$RADARR_KEY"
  UNPACKERR_LIDARR_API_KEY: "$LIDARR_KEY"
  UNPACKERR_READARR_API_KEY: "$READARR_KEY"
EOF

echo "[OK] Updated secret $NAMESPACE/media-stack-secrets with Arr/Prowlarr API keys."
echo "Enable/restart Unpackerr:"
echo "  kubectl -n $NAMESPACE apply -f k8s/unpackerr.yaml"
echo "  kubectl -n $NAMESPACE scale deploy/unpackerr --replicas=1"
