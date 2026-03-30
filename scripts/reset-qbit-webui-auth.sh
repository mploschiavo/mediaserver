#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
DEPLOYMENT="${DEPLOYMENT:-qbittorrent}"
ROLL_OUT_TIMEOUT="${ROLL_OUT_TIMEOUT:-5m}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  scripts/reset-qbit-webui-auth.sh

Description:
  Hard-resets qBittorrent WebUI auth in Kubernetes and reconciles it back to
  the credentials stored in media-stack secret.

  Steps:
  1) Locate qBittorrent.conf inside /config
  2) Remove stored WebUI username/password lines
  3) Restart qbittorrent deployment
  4) Run ensure-qbit-credentials.sh to set credentials from secret

Environment variables:
  NAMESPACE        (default: media-stack)
  DEPLOYMENT       (default: qbittorrent)
  ROLL_OUT_TIMEOUT (default: 5m)
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

echo "[INFO] Locating qBittorrent config file inside deploy/$DEPLOYMENT"
CONF_PATH="$(
  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$DEPLOYMENT" -- sh -c '
for p in \
  /config/qBittorrent/qBittorrent.conf \
  /config/qBittorrent/config/qBittorrent.conf \
  /config/qBittorrent/data/qBittorrent/config/qBittorrent.conf
do
  if [ -f "$p" ]; then
    echo "$p"
    exit 0
  fi
done
find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null | head -n1
' 2>/dev/null || true
)"

if [[ -z "$CONF_PATH" ]]; then
  echo "[ERR] Could not find qBittorrent.conf inside /config." >&2
  exit 1
fi

echo "[INFO] Found config: $CONF_PATH"
echo "[INFO] Backing up and clearing WebUI auth lines"
"${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$DEPLOYMENT" -- sh -c "
set -e
cp '$CONF_PATH' '${CONF_PATH}.bak.$(date +%s)'
sed -i \
  -e '/^WebUI\\\\Username=/d' \
  -e '/^WebUI\\\\Password_PBKDF2=/d' \
  -e '/^WebUI\\\\Password_ha1=/d' \
  '$CONF_PATH'
"

echo "[INFO] Restarting deploy/$DEPLOYMENT"
echo "[INFO] Scaling deploy/$DEPLOYMENT to 0 for clean stop"
"${KUBECTL[@]}" -n "$NAMESPACE" scale "deploy/$DEPLOYMENT" --replicas=0 >/dev/null

for _ in $(seq 1 60); do
  running="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=qbittorrent --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "${running:-0}" == "0" ]]; then
    break
  fi
  sleep 1
done

echo "[INFO] Scaling deploy/$DEPLOYMENT back to 1"
"${KUBECTL[@]}" -n "$NAMESPACE" scale "deploy/$DEPLOYMENT" --replicas=1 >/dev/null
if ! "${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/$DEPLOYMENT" --timeout="$ROLL_OUT_TIMEOUT"; then
  echo "[WARN] deploy/$DEPLOYMENT did not fully roll out in $ROLL_OUT_TIMEOUT; continuing." >&2
fi

echo "[INFO] Reconciling credentials from secret using ensure-qbit-credentials.sh"
QBIT_API_VALIDATION=0 QBIT_STRICT_LOGIN_CHECK=0 bash "$ROOT_DIR/scripts/ensure-qbit-credentials.sh"

echo "[OK] qBittorrent WebUI auth reset + reconciliation complete."
echo "[OK] Try logging in at http://qbittorrent.local with your secret credentials."
