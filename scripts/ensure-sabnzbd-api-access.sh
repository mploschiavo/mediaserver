#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
DEPLOYMENT="${SAB_DEPLOYMENT:-sabnzbd}"
SAB_HOST="${SAB_HOST:-sabnzbd}"
SAB_INGRESS_HOST="${SAB_INGRESS_HOST:-sabnzbd.local}"
SAB_LOCAL_RANGES="${SAB_LOCAL_RANGES:-10.0.0.0/8,172.16.0.0/12,192.168.0.0/16}"
SAB_HOST_WHITELIST_APPEND="${SAB_HOST_WHITELIST_APPEND:-}"
SAB_DOWNLOAD_DIR="${SAB_DOWNLOAD_DIR:-/data/usenet/incomplete}"
SAB_COMPLETE_DIR="${SAB_COMPLETE_DIR:-/data/usenet/completed}"
SAB_AUTO_BROWSER="${SAB_AUTO_BROWSER:-0}"

usage() {
  cat <<'EOF'
Usage:
  scripts/ensure-sabnzbd-api-access.sh

Description:
  Ensures SABnzbd API is reachable by Arr apps inside the cluster by reconciling
  `host_whitelist` and `local_ranges` in /config/sabnzbd.ini.

Environment variables:
  NAMESPACE                  (default: media-stack)
  SAB_DEPLOYMENT             (default: sabnzbd)
  SAB_HOST                   (default: sabnzbd)
  SAB_INGRESS_HOST           (default: sabnzbd.local)
  SAB_LOCAL_RANGES           (default: 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16)
  SAB_HOST_WHITELIST_APPEND  (optional comma-separated extra hosts)
  SAB_DOWNLOAD_DIR           (default: /data/usenet/incomplete)
  SAB_COMPLETE_DIR           (default: /data/usenet/completed)
  SAB_AUTO_BROWSER           (default: 0)
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

info() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }

if ! "${KUBECTL[@]}" -n "$NAMESPACE" get deploy "$DEPLOYMENT" >/dev/null 2>&1; then
  info "Deployment ${NAMESPACE}/${DEPLOYMENT} not found; skipping SAB API-access reconcile."
  exit 0
fi

info "Waiting for deploy/${DEPLOYMENT} rollout before SAB config reconcile"
"${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/${DEPLOYMENT}" --timeout=10m >/dev/null

cluster_host_1="${SAB_HOST}.${NAMESPACE}"
cluster_host_2="${SAB_HOST}.${NAMESPACE}.svc"
cluster_host_3="${SAB_HOST}.${NAMESPACE}.svc.cluster.local"

exec_out="$("${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/${DEPLOYMENT}" -- \
  env \
    SAB_HOST="$SAB_HOST" \
    SAB_CLUSTER_HOST_1="$cluster_host_1" \
    SAB_CLUSTER_HOST_2="$cluster_host_2" \
    SAB_CLUSTER_HOST_3="$cluster_host_3" \
    SAB_INGRESS_HOST="$SAB_INGRESS_HOST" \
    SAB_LOCAL_RANGES="$SAB_LOCAL_RANGES" \
    SAB_HOST_WHITELIST_APPEND="$SAB_HOST_WHITELIST_APPEND" \
    SAB_DOWNLOAD_DIR="$SAB_DOWNLOAD_DIR" \
    SAB_COMPLETE_DIR="$SAB_COMPLETE_DIR" \
    SAB_AUTO_BROWSER="$SAB_AUTO_BROWSER" \
  sh -lc '
set -eu
conf="/config/sabnzbd.ini"
[ -f "$conf" ] || { echo "__ERR__=missing_config"; exit 1; }

mkdir -p \
  "${SAB_DOWNLOAD_DIR}" \
  "${SAB_COMPLETE_DIR}" \
  "${SAB_COMPLETE_DIR}/tv" \
  "${SAB_COMPLETE_DIR}/movies" \
  "${SAB_COMPLETE_DIR}/music" \
  "${SAB_COMPLETE_DIR}/books" \
  "/config/Downloads/incomplete" \
  "/config/Downloads/complete"
chmod -R 0777 "${SAB_DOWNLOAD_DIR}" "${SAB_COMPLETE_DIR}" "/config/Downloads" 2>/dev/null || true

current_hw="$(awk -F "=" '"'"'/^host_whitelist[[:space:]]*=/{print $2; exit}'"'"' "$conf" | tr -d " " || true)"
current_lr="$(awk -F "=" '"'"'/^local_ranges[[:space:]]*=/{print $2; exit}'"'"' "$conf" | tr -d " " || true)"

dedupe_csv() {
  printf "%s" "$1" \
    | tr "," "\n" \
    | sed "s/^[[:space:]]*//;s/[[:space:]]*$//" \
    | awk "NF && !seen[\$0]++" \
    | paste -sd "," -
}

desired_hw="$(dedupe_csv "${current_hw},${SAB_HOST},${SAB_CLUSTER_HOST_1},${SAB_CLUSTER_HOST_2},${SAB_CLUSTER_HOST_3},${SAB_INGRESS_HOST},localhost,127.0.0.1,${SAB_HOST_WHITELIST_APPEND}")"
desired_lr="$(dedupe_csv "${current_lr},${SAB_LOCAL_RANGES}")"

before="$(grep -E "^(host_whitelist|local_ranges|download_dir|complete_dir|auto_browser)[[:space:]]*=" "$conf" 2>/dev/null || true)"

if grep -q "^host_whitelist[[:space:]]*=" "$conf"; then
  sed -i "s#^host_whitelist[[:space:]]*=.*#host_whitelist = ${desired_hw}#" "$conf"
else
  echo "host_whitelist = ${desired_hw}" >>"$conf"
fi

if grep -q "^local_ranges[[:space:]]*=" "$conf"; then
  sed -i "s#^local_ranges[[:space:]]*=.*#local_ranges = ${desired_lr}#" "$conf"
else
  echo "local_ranges = ${desired_lr}" >>"$conf"
fi

if grep -q "^download_dir[[:space:]]*=" "$conf"; then
  sed -i "s#^download_dir[[:space:]]*=.*#download_dir = ${SAB_DOWNLOAD_DIR}#" "$conf"
else
  echo "download_dir = ${SAB_DOWNLOAD_DIR}" >>"$conf"
fi

if grep -q "^complete_dir[[:space:]]*=" "$conf"; then
  sed -i "s#^complete_dir[[:space:]]*=.*#complete_dir = ${SAB_COMPLETE_DIR}#" "$conf"
else
  echo "complete_dir = ${SAB_COMPLETE_DIR}" >>"$conf"
fi

if grep -q "^auto_browser[[:space:]]*=" "$conf"; then
  sed -i "s#^auto_browser[[:space:]]*=.*#auto_browser = ${SAB_AUTO_BROWSER}#" "$conf"
else
  echo "auto_browser = ${SAB_AUTO_BROWSER}" >>"$conf"
fi

after="$(grep -E "^(host_whitelist|local_ranges|download_dir|complete_dir|auto_browser)[[:space:]]*=" "$conf" 2>/dev/null || true)"

changed=0
[ "$before" = "$after" ] || changed=1

echo "__CHANGED__=${changed}"
echo "__HOST_WHITELIST__=${desired_hw}"
echo "__LOCAL_RANGES__=${desired_lr}"
echo "__DOWNLOAD_DIR__=${SAB_DOWNLOAD_DIR}"
echo "__COMPLETE_DIR__=${SAB_COMPLETE_DIR}"
echo "__AUTO_BROWSER__=${SAB_AUTO_BROWSER}"
')"

changed="$(printf '%s\n' "$exec_out" | awk -F= '/^__CHANGED__=/{print $2; exit}')"
host_whitelist="$(printf '%s\n' "$exec_out" | awk -F= '/^__HOST_WHITELIST__=/{print $2; exit}')"
local_ranges="$(printf '%s\n' "$exec_out" | awk -F= '/^__LOCAL_RANGES__=/{print $2; exit}')"
download_dir="$(printf '%s\n' "$exec_out" | awk -F= '/^__DOWNLOAD_DIR__=/{print $2; exit}')"
complete_dir="$(printf '%s\n' "$exec_out" | awk -F= '/^__COMPLETE_DIR__=/{print $2; exit}')"
auto_browser="$(printf '%s\n' "$exec_out" | awk -F= '/^__AUTO_BROWSER__=/{print $2; exit}')"

if [[ -z "$changed" ]]; then
  warn "Could not determine whether SAB config changed. Raw output:"
  printf '%s\n' "$exec_out"
  changed="1"
fi

info "SAB host_whitelist: ${host_whitelist}"
info "SAB local_ranges: ${local_ranges}"
info "SAB download_dir: ${download_dir}"
info "SAB complete_dir: ${complete_dir}"
info "SAB auto_browser: ${auto_browser}"

if [[ "$changed" == "1" ]]; then
  info "SAB config changed; restarting deploy/${DEPLOYMENT}"
  "${KUBECTL[@]}" -n "$NAMESPACE" rollout restart "deploy/${DEPLOYMENT}" >/dev/null
  "${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/${DEPLOYMENT}" --timeout=10m >/dev/null
  info "SAB API-access reconcile complete (restart applied)."
else
  info "SAB API-access already configured; no restart needed."
fi
