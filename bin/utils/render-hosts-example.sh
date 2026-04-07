#!/usr/bin/env bash
set -euo pipefail

IP="${1:-192.168.1.50}"
NAMESPACE="${2:-media-stack}"
INGRESS_NAME="${INGRESS_NAME:-media-stack-ingress}"

if [[ "$IP" == "-h" || "$IP" == "--help" ]]; then
  cat <<'EOF'
Usage:
  bin/render-hosts-example.sh <NODE_IP> [NAMESPACE]

Description:
  Prints a single hosts-file line containing all ingress hosts for the namespace.
EOF
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

hosts="$("${KUBECTL[@]}" -n "$NAMESPACE" get ingress "$INGRESS_NAME" -o jsonpath='{range .spec.rules[*]}{.host}{" "}{end}' 2>/dev/null || true)"

if [[ -z "$hosts" ]]; then
  hosts="homepage.local jellyfin.local jellyseerr.local sonarr.local radarr.local lidarr.local readarr.local bazarr.local prowlarr.local qbittorrent.local sabnzbd.local maintainerr.local tautulli.local traefik.local"
fi

clean_hosts="$(printf '%s\n' "$hosts" | tr ' ' '\n' | sed '/^$/d' | sort -u | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
echo "$IP $clean_hosts"
