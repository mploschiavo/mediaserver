#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${1:-$ROOT_DIR/bootstrap/media-stack.bootstrap.json}"
ENABLE_UNPACKERR="${ENABLE_UNPACKERR:-1}"
SECRET_NAME="${SECRET_NAME:-media-stack-secrets}"
RUN_START_EPOCH="$(date +%s)"
CURRENT_PHASE=""
CURRENT_PHASE_START=0
declare -a PHASE_NAMES=()
declare -a PHASE_RESULTS=()
declare -a PHASE_SECONDS=()

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
info() { echo "[$(ts)] [INFO] $*"; }
warn() { echo "[$(ts)] [WARN] $*" >&2; }
err() { echo "[$(ts)] [ERR] $*" >&2; }

escape_sed_replacement() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//&/\\&}"
  printf '%s' "$value"
}

manifest_overrides() {
  local ns_escaped root_escaped
  ns_escaped="$(escape_sed_replacement "$NAMESPACE")"
  root_escaped="$(escape_sed_replacement "$PREPARE_HOST_ROOT")"

  sed \
    -e "s#namespace:[[:space:]]*media-stack#namespace: ${ns_escaped}#g" \
    -e "s#name:[[:space:]]*media-stack\$#name: ${ns_escaped}#g" \
    -e "s#/srv/media-stack#${root_escaped}#g"
}

phase_start() {
  CURRENT_PHASE="$1"
  CURRENT_PHASE_START="$(date +%s)"
  info "[PHASE] START: $CURRENT_PHASE"
}

phase_end() {
  local result="${1:-ok}"
  local now elapsed
  now="$(date +%s)"
  if [[ -n "$CURRENT_PHASE" && "$CURRENT_PHASE_START" -gt 0 ]]; then
    elapsed=$((now - CURRENT_PHASE_START))
    PHASE_NAMES+=("$CURRENT_PHASE")
    PHASE_RESULTS+=("$result")
    PHASE_SECONDS+=("$elapsed")
    case "$result" in
      ok) info "[PHASE] DONE: $CURRENT_PHASE (${elapsed}s)" ;;
      skipped) info "[PHASE] SKIP: $CURRENT_PHASE (${elapsed}s)" ;;
      *) warn "[PHASE] FAIL: $CURRENT_PHASE (${elapsed}s)" ;;
    esac
  fi
  CURRENT_PHASE=""
  CURRENT_PHASE_START=0
}

print_phase_summary() {
  local total i
  total=$(( $(date +%s) - RUN_START_EPOCH ))
  info "Phase Summary (total ${total}s)"
  if [[ "${#PHASE_NAMES[@]}" -eq 0 ]]; then
    info "  (no phases recorded)"
    return 0
  fi
  for i in "${!PHASE_NAMES[@]}"; do
    info "  ${PHASE_NAMES[$i]} => ${PHASE_RESULTS[$i]} (${PHASE_SECONDS[$i]}s)"
  done
}

on_error() {
  local code="$?"
  local line="$1"
  local cmd="$2"
  if [[ -n "$CURRENT_PHASE" ]]; then
    phase_end "failed"
  fi
  err "bootstrap-all failed at line ${line} while running: ${cmd}"
  print_phase_summary
  exit "$code"
}

trap 'on_error $LINENO "$BASH_COMMAND"' ERR

usage() {
  cat <<'EOF'
Usage:
  scripts/bootstrap-all.sh [CONFIG_FILE]

Description:
  End-to-end bootstrap for media-stack after pods are healthy:
  1) Ensure qBittorrent credentials secret + WebUI credentials are aligned
  2) Ensure Jellyfin startup + API key are fully automated (no manual wizard)
  3) Ensure SABnzbd API access is reachable from Arr pods (host whitelist/local ranges)
  4) Arr + Prowlarr + Jellyseerr wiring (root folders, app links, downloader clients/categories, Jellyseerr Sonarr/Radarr mappings)
  5) Seed a Jellyseerr local admin account from STACK_ADMIN_USERNAME / STACK_ADMIN_PASSWORD
  6) Auto-add tested Prowlarr indexers
  7) Sync Arr API keys into Unpackerr secret
  8) Optionally enable/restart Unpackerr

Environment variables:
  ENABLE_UNPACKERR  (default: 1)
  NAMESPACE         (default: media-stack)
  SECRET_NAME       (default: media-stack-secrets)
  PREPARE_HOST_ROOT (default: /srv/media-stack)
  SKIP_QBIT_ENSURE  (default: 0)
  SKIP_SAB_ENSURE   (default: 0)
  SKIP_JELLYFIN_BOOTSTRAP (default: 0)
EOF
}

if [[ "$CONFIG_FILE" == "-h" || "$CONFIG_FILE" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  err "Config file not found: $CONFIG_FILE"
  exit 1
fi

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  err "Neither microk8s nor kubectl is available in PATH."
  exit 1
fi

NAMESPACE="${NAMESPACE:-media-stack}"
PREPARE_HOST_ROOT="${PREPARE_HOST_ROOT:-/srv/media-stack}"
SKIP_QBIT_ENSURE="${SKIP_QBIT_ENSURE:-0}"
SKIP_SAB_ENSURE="${SKIP_SAB_ENSURE:-0}"
SKIP_JELLYFIN_BOOTSTRAP="${SKIP_JELLYFIN_BOOTSTRAP:-0}"

config_probe_default_true() {
  local probe="$1"
  local result=""
  result="$(python3 - "$CONFIG_FILE" "$probe" <<'PY' 2>/dev/null || true
import json
import sys

def resolved_client(cfg, role_key, default_key):
    bindings = cfg.get("technology_bindings") or {}
    clients = cfg.get("download_clients") or {}
    if not isinstance(bindings, dict):
        bindings = {}
    if not isinstance(clients, dict):
        clients = {}

    selected = str(bindings.get(role_key, default_key) or "").strip().lower() or default_key
    selected_cfg = clients.get(selected)
    if isinstance(selected_cfg, dict):
        return selected_cfg
    fallback_cfg = clients.get(default_key)
    if isinstance(fallback_cfg, dict):
        return fallback_cfg
    return {}

try:
    cfg = json.load(open(sys.argv[1], "r", encoding="utf-8"))
    probe = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
    if probe == "torrent-ensure":
        client_cfg = resolved_client(cfg, "torrent_client", "qbittorrent")
        enabled = bool(
            client_cfg.get("configure_arr_clients")
            or client_cfg.get("set_categories_in_qbit")
            or client_cfg.get("set_categories")
        )
    elif probe == "usenet-ensure":
        client_cfg = resolved_client(cfg, "usenet_client", "sabnzbd")
        enabled = bool(client_cfg.get("configure_arr_clients"))
    else:
        raise RuntimeError(f"Unknown config probe: {probe}")
    print("1" if enabled else "0")
except Exception:
    print("error")
PY
)"
  if [[ "$result" == "1" ]]; then
    return 0
  fi
  if [[ "$result" == "0" ]]; then
    return 1
  fi
  # Preserve previous behavior if config parsing or evaluation fails.
  return 0
}

should_run_qbit_ensure=0
if config_probe_default_true "torrent-ensure"; then
  should_run_qbit_ensure=1
fi

should_run_sab_ensure=0
if config_probe_default_true "usenet-ensure"; then
  should_run_sab_ensure=1
fi

if [[ "$SKIP_QBIT_ENSURE" != "1" && "$should_run_qbit_ensure" == "1" ]]; then
  phase_start "Ensure qBittorrent credentials"
  info "Step 1/7: Ensuring qBittorrent credentials are config-as-code and usable"
  NAMESPACE="$NAMESPACE" PREPARE_HOST_ROOT="$PREPARE_HOST_ROOT" bash "$ROOT_DIR/scripts/ensure-qbit-credentials.sh"
  phase_end "ok"
else
  phase_start "Ensure qBittorrent credentials"
  if [[ "$SKIP_QBIT_ENSURE" != "1" ]]; then
    info "Torrent client ensure skipped: active torrent client is not configured for bootstrap."
  fi
  phase_end "skipped"
fi

if [[ "$SKIP_JELLYFIN_BOOTSTRAP" != "1" ]]; then
  phase_start "Ensure Jellyfin bootstrap and API key"
  info "Step 2/7: Completing Jellyfin startup and syncing API key into secret"
  NAMESPACE="$NAMESPACE" SECRET_NAME="$SECRET_NAME" bash "$ROOT_DIR/scripts/ensure-jellyfin-bootstrap.sh"
  phase_end "ok"
else
  phase_start "Ensure Jellyfin bootstrap and API key"
  phase_end "skipped"
fi

if [[ "$SKIP_SAB_ENSURE" != "1" && "$should_run_sab_ensure" == "1" ]]; then
  phase_start "Ensure SABnzbd API access"
  info "Step 3/7: Ensuring SABnzbd API is reachable from Arr pods"
  NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/ensure-sabnzbd-api-access.sh"
  phase_end "ok"
else
  phase_start "Ensure SABnzbd API access"
  if [[ "$SKIP_SAB_ENSURE" != "1" ]]; then
    info "Usenet client ensure skipped: active usenet client is not configured for bootstrap."
  fi
  phase_end "skipped"
fi

phase_start "Run Arr/Prowlarr/Jellyseerr bootstrap job"
info "Step 4/7: Running Arr/Prowlarr/Jellyseerr bootstrap job"
NAMESPACE="$NAMESPACE" SKIP_QBIT_ENSURE=1 SKIP_SAB_ENSURE=1 PREPARE_HOST_ROOT="$PREPARE_HOST_ROOT" bash "$ROOT_DIR/scripts/run-bootstrap-job.sh" "$CONFIG_FILE"
phase_end "ok"

phase_start "Seed Jellyseerr local admin"
info "Step 5/7: Seeding Jellyseerr local admin from stack secret"
NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/seed-jellyseerr-local-admin.sh"
phase_end "ok"

phase_start "Run Prowlarr auto-indexer discovery"
info "Step 6/7: Running Prowlarr auto-indexer discovery"
NAMESPACE="$NAMESPACE" PREPARE_HOST_ROOT="$PREPARE_HOST_ROOT" bash "$ROOT_DIR/scripts/run-prowlarr-auto-indexers.sh"
phase_end "ok"

phase_start "Sync Unpackerr API keys"
info "Step 7/7: Syncing Unpackerr API keys"
NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/sync-unpackerr-keys.sh"
phase_end "ok"

if [[ "$ENABLE_UNPACKERR" == "1" ]]; then
  phase_start "Enable Unpackerr deployment"
  info "Enabling Unpackerr deployment"
  manifest_overrides <"$ROOT_DIR/k8s/unpackerr.yaml" | "${KUBECTL[@]}" apply -f -
  "${KUBECTL[@]}" -n "$NAMESPACE" scale deploy/unpackerr --replicas=1
  "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deploy/unpackerr --timeout=10m
  phase_end "ok"
else
  phase_start "Enable Unpackerr deployment"
  phase_end "skipped"
fi

info "Full bootstrap complete."
print_phase_summary
