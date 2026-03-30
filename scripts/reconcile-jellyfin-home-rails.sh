#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-media-stack}"
CONFIG_FILE="${1:-$ROOT_DIR/bootstrap/media-stack.bootstrap.json}"
LOCAL_PORT="${LOCAL_PORT:-18096}"
FORCE_ENABLE="${FORCE_ENABLE:-0}"

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
info() { echo "[$(ts)] [INFO] $*"; }
warn() { echo "[$(ts)] [WARN] $*" >&2; }
err() { echo "[$(ts)] [ERR] $*" >&2; }

usage() {
  cat <<'EOF'
Usage:
  scripts/reconcile-jellyfin-home-rails.sh [CONFIG_FILE]
  scripts/reconcile-jellyfin-home-rails.sh --force-enable [CONFIG_FILE]

Description:
  Reconciles Jellyfin home-rails behavior using bootstrap config.
  By default this respects jellyfin_home_rails.enabled and cleanup settings.
  Use --force-enable only when you explicitly want synthetic rail collections recreated.
EOF
}

choose_kubectl() {
  if command -v microk8s >/dev/null 2>&1; then
    echo "microk8s kubectl"
    return
  fi
  if command -v kubectl >/dev/null 2>&1; then
    echo "kubectl"
    return
  fi
  err "kubectl not found in PATH."
  exit 1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${1:-}" == "--force-enable" ]]; then
  CONFIG_FILE="${2:-$ROOT_DIR/bootstrap/media-stack.bootstrap.json}"
  FORCE_ENABLE=1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  err "Config file not found: $CONFIG_FILE"
  exit 1
fi

KUBECTL_STR="$(choose_kubectl)"
IFS=' ' read -r -a KUBECTL <<<"$KUBECTL_STR"

info "Namespace: $NAMESPACE"
info "Config: $CONFIG_FILE"

JELLYFIN_API_KEY="$("${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets -o jsonpath='{.data.JELLYFIN_API_KEY}' | base64 -d || true)"
if [[ -z "${JELLYFIN_API_KEY:-}" ]]; then
  err "Could not read JELLYFIN_API_KEY from secret ${NAMESPACE}/media-stack-secrets."
  exit 1
fi
export JELLYFIN_API_KEY

PF_LOG="$(mktemp -t media-stack-jf-pf.XXXXXX)"
cleanup() {
  if [[ -n "${PF_PID:-}" ]] && kill -0 "$PF_PID" >/dev/null 2>&1; then
    kill "$PF_PID" >/dev/null 2>&1 || true
  fi
  rm -f "$PF_LOG" || true
}
trap cleanup EXIT INT TERM

info "Starting port-forward on 127.0.0.1:${LOCAL_PORT} -> svc/jellyfin:8096"
"${KUBECTL[@]}" -n "$NAMESPACE" port-forward svc/jellyfin "${LOCAL_PORT}:8096" >"$PF_LOG" 2>&1 &
PF_PID="$!"

for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${LOCAL_PORT}/System/Info/Public" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! curl -fsS "http://127.0.0.1:${LOCAL_PORT}/System/Info/Public" >/dev/null 2>&1; then
  err "Could not reach Jellyfin through local port-forward."
  warn "port-forward logs:"
  sed -n '1,120p' "$PF_LOG" >&2 || true
  exit 1
fi

info "Reconciling Jellyfin home rails via bootstrap logic"
python3 - "$ROOT_DIR" "$CONFIG_FILE" "$LOCAL_PORT" "$FORCE_ENABLE" <<'PY'
import copy
import importlib.util
import json
import sys
from pathlib import Path

root_dir = Path(sys.argv[1]).resolve()
config_path = Path(sys.argv[2]).resolve()
local_port = int(sys.argv[3])
force_enable = str(sys.argv[4]).strip() == "1"

sys.path.insert(0, str(root_dir / "scripts"))
spec = importlib.util.spec_from_file_location(
    "bootstrap_apps", root_dir / "scripts" / "bootstrap-apps.py"
)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

cfg = json.loads(config_path.read_text(encoding="utf-8"))
rails_cfg = copy.deepcopy(cfg.get("jellyfin_home_rails") or {})
if force_enable:
    rails_cfg["enabled"] = True
rails_cfg["url"] = f"http://127.0.0.1:{local_port}"
rails_cfg["api_key_env"] = "JELLYFIN_API_KEY"
rails_cfg["auto_discover_api_key_from_db"] = False
rails_cfg["auto_discover_user_id"] = True
cfg["jellyfin_home_rails"] = rails_cfg

module.ensure_jellyfin_home_rails(cfg, str(root_dir), 180)
print("Jellyfin home rails reconcile complete.")
PY

if [[ "$FORCE_ENABLE" == "1" ]]; then
  info "Done (forced enable). Hard-refresh Jellyfin and re-open Collections."
else
  info "Done. Hard-refresh Jellyfin and re-open Movies/Home."
fi
