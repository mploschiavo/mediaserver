#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-/srv/media-stack}"
PUID="${PUID:-911}"
PGID="${PGID:-911}"

if [[ "$ROOT" == "-h" || "$ROOT" == "--help" ]]; then
  cat <<'EOF'
Usage:
  sudo PUID=911 PGID=911 bash bin/fix-media-perms.sh [ROOT]

Description:
  Fixes ownership and write permissions for media-stack hostPath directories.
  Default ROOT is /srv/media-stack.
EOF
  exit 0
fi

if [[ "$EUID" -ne 0 ]]; then
  echo "Run as root so ownership can be corrected."
  echo "Example:"
  echo "  sudo PUID=$PUID PGID=$PGID bash bin/fix-media-perms.sh $ROOT"
  exit 1
fi

mkdir -p "$ROOT/config" "$ROOT/data" "$ROOT/media"

chown -R "$PUID:$PGID" "$ROOT/config" "$ROOT/data" "$ROOT/media"
chmod -R u+rwX,g+rwX "$ROOT/config" "$ROOT/data" "$ROOT/media"
find "$ROOT/config" "$ROOT/data" "$ROOT/media" -type d -exec chmod g+s {} +

echo "Permissions fixed for $ROOT/{config,data,media} -> $PUID:$PGID"
echo "Restart affected apps:"
echo "  kubectl -n media-stack rollout restart deploy/sonarr deploy/radarr deploy/lidarr deploy/readarr deploy/bazarr deploy/prowlarr deploy/qbittorrent"
