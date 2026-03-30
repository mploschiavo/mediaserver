#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
STACK_ROOT="${STACK_ROOT:-/srv/media-stack}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
INCLUDE_MEDIA="${INCLUDE_MEDIA:-0}"

usage() {
  cat <<'EOF'
Usage:
  scripts/backup-stack.sh

Description:
  Creates a backup bundle with:
  - stack config directory
  - stack data directory
  - optional media directory
  - Kubernetes secret export for media-stack-secrets

Environment variables:
  NAMESPACE      (default: media-stack)
  STACK_ROOT     (default: /srv/media-stack)
  BACKUP_DIR     (default: ./backups)
  INCLUDE_MEDIA  (default: 0; set to 1 to include /media)
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

timestamp="$(date +%Y%m%d-%H%M%S)"
bundle_dir="${BACKUP_DIR}/media-stack-backup-${timestamp}"
mkdir -p "$bundle_dir"

for d in config data; do
  if [[ -d "${STACK_ROOT}/${d}" ]]; then
    cp -a "${STACK_ROOT}/${d}" "$bundle_dir/"
  fi
done

if [[ "$INCLUDE_MEDIA" == "1" && -d "${STACK_ROOT}/media" ]]; then
  cp -a "${STACK_ROOT}/media" "$bundle_dir/"
fi

"${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets -o yaml >"${bundle_dir}/media-stack-secrets.yaml" 2>/dev/null || true

cat >"${bundle_dir}/backup-metadata.txt" <<EOF
timestamp=${timestamp}
namespace=${NAMESPACE}
stack_root=${STACK_ROOT}
include_media=${INCLUDE_MEDIA}
EOF

archive="${bundle_dir}.tar.gz"
tar -C "$BACKUP_DIR" -czf "$archive" "$(basename "$bundle_dir")"
rm -rf "$bundle_dir"

echo "[OK] Backup created: $archive"
