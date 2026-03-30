#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-/srv/media-stack}"
PUID="${PUID:-911}"
PGID="${PGID:-911}"

mkdir -p \
  "$ROOT/config/jellyfin" "$ROOT/config/plex" "$ROOT/config/jellyseerr" \
  "$ROOT/config/sonarr" "$ROOT/config/radarr" "$ROOT/config/lidarr" "$ROOT/config/readarr" \
  "$ROOT/config/bazarr" "$ROOT/config/prowlarr" "$ROOT/config/qbittorrent" "$ROOT/config/sabnzbd" \
  "$ROOT/config/unpackerr" "$ROOT/config/tautulli" "$ROOT/config/homepage" "$ROOT/config/traefik" \
  "$ROOT/config/jellyfin-auto-collections" \
  "$ROOT/config/sabnzbd/Downloads/incomplete" "$ROOT/config/sabnzbd/Downloads/complete" \
  "$ROOT/data/torrents/incomplete" "$ROOT/data/torrents/completed/tv" "$ROOT/data/torrents/completed/movies" \
  "$ROOT/data/torrents/completed/music" "$ROOT/data/torrents/completed/books" "$ROOT/data/torrents/watch" \
  "$ROOT/data/usenet/incomplete" "$ROOT/data/usenet/completed" "$ROOT/data/usenet/completed/tv" \
  "$ROOT/data/usenet/completed/movies" "$ROOT/data/usenet/completed/music" "$ROOT/data/usenet/completed/books" \
  "$ROOT/data/transcode" \
  "$ROOT/media/movies" "$ROOT/media/tv" "$ROOT/media/music" "$ROOT/media/books" "$ROOT/media/audiobooks" "$ROOT/media/podcasts" \
  "$ROOT/backups"

if [[ "${SKIP_PERMS:-0}" != "1" ]]; then
  if [[ "$EUID" -eq 0 ]]; then
    chown -R "$PUID:$PGID" "$ROOT/config" "$ROOT/data" "$ROOT/media"
    chmod -R u+rwX,g+rwX "$ROOT/config" "$ROOT/data" "$ROOT/media"
    find "$ROOT/config" "$ROOT/data" "$ROOT/media" -type d -exec chmod g+s {} +
    echo "Set ownership/permissions for $ROOT/{config,data,media} to $PUID:$PGID"
  else
    echo "Prepared host folders at $ROOT"
    echo "Permission fix skipped (not root). Run with sudo to set ownership:"
    echo "  sudo PUID=$PUID PGID=$PGID bash scripts/prepare-host.sh $ROOT"
    exit 0
  fi
fi

echo "Prepared host folders at $ROOT"
