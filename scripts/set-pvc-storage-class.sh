#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_FILE="$ROOT_DIR/k8s/storage-pvc.yaml"
CLASS_NAME=""
CLEAR_MODE=0

usage() {
  cat <<'USAGE'
Usage:
  scripts/set-pvc-storage-class.sh <STORAGE_CLASS_NAME> [--file PATH]
  scripts/set-pvc-storage-class.sh --clear [--file PATH]

Description:
  Adds or updates spec.storageClassName on every PVC in the target manifest.
  This lets you switch storage backends without touching Deployment YAML files.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file)
      TARGET_FILE="${2:-}"
      shift 2
      ;;
    --clear)
      CLEAR_MODE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$CLASS_NAME" ]]; then
        CLASS_NAME="$1"
        shift
      else
        echo "[ERR] Unknown argument: $1" >&2
        usage
        exit 1
      fi
      ;;
  esac
done

if [[ ! -f "$TARGET_FILE" ]]; then
  echo "[ERR] File not found: $TARGET_FILE" >&2
  exit 1
fi

if [[ "$CLEAR_MODE" != "1" && -z "$CLASS_NAME" ]]; then
  echo "[ERR] STORAGE_CLASS_NAME is required unless --clear is used." >&2
  usage
  exit 1
fi

TMP_FILE="$(mktemp -t media-stack-storage-class.XXXXXX)"

if [[ "$CLEAR_MODE" == "1" ]]; then
  awk 'BEGIN{in_pvc=0}
    /^kind:[[:space:]]*PersistentVolumeClaim[[:space:]]*$/ {in_pvc=1; print; next}
    /^---[[:space:]]*$/ {in_pvc=0; print; next}
    {
      if (in_pvc && $0 ~ /^[[:space:]]*storageClassName:[[:space:]]*/) next
      print
    }' "$TARGET_FILE" >"$TMP_FILE"
  mv "$TMP_FILE" "$TARGET_FILE"
  echo "[OK] Cleared storageClassName from PVCs in $TARGET_FILE"
  exit 0
fi

awk -v cls="$CLASS_NAME" '
  BEGIN {in_pvc=0; in_spec=0; inserted=0}
  /^kind:[[:space:]]*PersistentVolumeClaim[[:space:]]*$/ {in_pvc=1; in_spec=0; inserted=0; print; next}
  /^---[[:space:]]*$/ {
    if (in_pvc && in_spec && !inserted) {
      print "  storageClassName: " cls
    }
    in_pvc=0; in_spec=0; inserted=0
    print
    next
  }
  {
    if (in_pvc && $0 ~ /^[[:space:]]*spec:[[:space:]]*$/) {
      in_spec=1
      print
      next
    }
    if (in_pvc && in_spec && $0 ~ /^[[:space:]]*storageClassName:[[:space:]]*/) {
      print "  storageClassName: " cls
      inserted=1
      next
    }
    if (in_pvc && in_spec && !inserted && $0 ~ /^[[:space:]]*resources:[[:space:]]*$/) {
      print "  storageClassName: " cls
      inserted=1
    }
    print
  }
  END {
    if (in_pvc && in_spec && !inserted) {
      print "  storageClassName: " cls
    }
  }
' "$TARGET_FILE" >"$TMP_FILE"

mv "$TMP_FILE" "$TARGET_FILE"
echo "[OK] Set storageClassName=$CLASS_NAME on PVCs in $TARGET_FILE"
