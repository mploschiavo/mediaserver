#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${1:-/srv/media-stack}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_DIR="$ROOT/backups"
OUT_FILE="$OUT_DIR/config-backup-$STAMP.tgz"

echo "[WARN] scripts/backup-configs.sh is deprecated."
echo "[WARN] Use scripts/backup-stack.sh for full stack backups (config + data + secret export)."

mkdir -p "$OUT_DIR"
tar -czf "$OUT_FILE" "$ROOT/config"
echo "Backup written: $OUT_FILE"
