#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
STACK_ROOT="${STACK_ROOT:-/srv/media-stack}"
ARCHIVE_PATH="${1:-}"
RESTORE_MEDIA="${RESTORE_MEDIA:-0}"

usage() {
  cat <<'EOF'
Usage:
  scripts/restore-stack.sh <BACKUP_ARCHIVE.tar.gz>

Description:
  Restores a backup produced by scripts/backup-stack.sh:
  - config and data directories
  - optional media directory when RESTORE_MEDIA=1
  - media-stack-secrets Kubernetes secret if present in backup

Environment variables:
  NAMESPACE     (default: media-stack)
  STACK_ROOT    (default: /srv/media-stack)
  RESTORE_MEDIA (default: 0)
EOF
}

if [[ -z "$ARCHIVE_PATH" || "$ARCHIVE_PATH" == "-h" || "$ARCHIVE_PATH" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "[ERR] Backup archive not found: $ARCHIVE_PATH" >&2
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

tmp_dir="$(mktemp -d)"
cleanup() { rm -rf "$tmp_dir"; }
trap cleanup EXIT

tar -C "$tmp_dir" -xzf "$ARCHIVE_PATH"
restore_root="$(find "$tmp_dir" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[[ -n "$restore_root" ]] || { echo "[ERR] Invalid backup archive structure." >&2; exit 1; }

mkdir -p "$STACK_ROOT"

for d in config data; do
  if [[ -d "${restore_root}/${d}" ]]; then
    mkdir -p "${STACK_ROOT}/${d}"
    cp -a "${restore_root}/${d}/." "${STACK_ROOT}/${d}/"
  fi
done

if [[ "$RESTORE_MEDIA" == "1" && -d "${restore_root}/media" ]]; then
  mkdir -p "${STACK_ROOT}/media"
  cp -a "${restore_root}/media/." "${STACK_ROOT}/media/"
fi

if [[ -f "${restore_root}/media-stack-secrets.yaml" ]]; then
  "${KUBECTL[@]}" apply -f "${restore_root}/media-stack-secrets.yaml"
fi

echo "[OK] Restore complete from $ARCHIVE_PATH"
