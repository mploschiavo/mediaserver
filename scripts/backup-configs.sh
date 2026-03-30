#!/usr/bin/env bash
set -euo pipefail
ROOT=${1:-/srv/media-stack}
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$ROOT/backups"
tar -czf "$ROOT/backups/config-backup-$STAMP.tgz" "$ROOT/config"
echo "Backup written: $ROOT/backups/config-backup-$STAMP.tgz"
