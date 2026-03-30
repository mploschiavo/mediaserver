#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
SECRET_NAME="${SECRET_NAME:-media-stack-secrets}"
DEFAULT_STACK_ADMIN_USER="${DEFAULT_STACK_ADMIN_USER:-admin}"
DEFAULT_STACK_ADMIN_PASS="${DEFAULT_STACK_ADMIN_PASS:-media-stack-admin}"
DEFAULT_QBIT_USER="${DEFAULT_QBIT_USER:-$DEFAULT_STACK_ADMIN_USER}"
DEFAULT_QBIT_PASS="${DEFAULT_QBIT_PASS:-$DEFAULT_STACK_ADMIN_PASS}"
LOCAL_PORT="${LOCAL_PORT:-18080}"
ROLL_OUT_TIMEOUT="${ROLL_OUT_TIMEOUT:-5m}"
QBIT_WAIT_SECONDS="${QBIT_WAIT_SECONDS:-120}"
QBIT_API_URL="${QBIT_API_URL:-}"
QBIT_DEPLOYMENT="${QBIT_DEPLOYMENT:-qbittorrent}"
FORCE_RESET_ON_AUTH_FAILURE="${FORCE_RESET_ON_AUTH_FAILURE:-1}"
QBIT_LOG_VERBOSE="${QBIT_LOG_VERBOSE:-1}"
QBIT_FORCE_CONFIG_SYNC="${QBIT_FORCE_CONFIG_SYNC:-1}"
QBIT_STRICT_LOGIN_CHECK="${QBIT_STRICT_LOGIN_CHECK:-0}"
QBIT_API_VALIDATION="${QBIT_API_VALIDATION:-0}"
QBIT_USE_STACK_ADMIN="${QBIT_USE_STACK_ADMIN:-1}"

usage() {
  cat <<'EOF'
Usage:
  scripts/ensure-qbit-credentials.sh

Description:
  Ensures qBittorrent credentials are fully config-as-code:
  1) Creates/patches media-stack secret with default credentials when missing
  2) Ensures qBittorrent WebUI credentials match the secret values

Environment variables:
  NAMESPACE          (default: media-stack)
  SECRET_NAME        (default: media-stack-secrets)
  DEFAULT_STACK_ADMIN_USER (default: admin)
  DEFAULT_STACK_ADMIN_PASS (default: media-stack-admin)
  DEFAULT_QBIT_USER  (default: DEFAULT_STACK_ADMIN_USER)
  DEFAULT_QBIT_PASS  (default: DEFAULT_STACK_ADMIN_PASS)
  LOCAL_PORT         (default: 18080)
  ROLL_OUT_TIMEOUT   (default: 5m)
  QBIT_WAIT_SECONDS  (default: 120)
  QBIT_API_URL       (optional; if set, use this URL directly instead of port-forward)
  QBIT_DEPLOYMENT    (default: qbittorrent)
  FORCE_RESET_ON_AUTH_FAILURE (default: 1; clears qB auth config and retries once)
  QBIT_LOG_VERBOSE   (default: 1; logs auth-attempt details without passwords)
  QBIT_FORCE_CONFIG_SYNC (default: 1; writes username + PBKDF2 password hash directly to qB config)
  QBIT_STRICT_LOGIN_CHECK (default: 0; if 1, fail when API validation cannot authenticate)
  QBIT_API_VALIDATION (default: 0; when 1 validates login via qB API/port-forward)
  QBIT_USE_STACK_ADMIN (default: 1; keep qB credentials equal to stack admin)
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

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  printf '%s' "$s"
}

require_python3() {
  if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERR] python3 is required for deterministic qB password hash generation." >&2
    exit 1
  fi
}

generate_qbit_pbkdf2_hash() {
  local password="$1"
  python3 - "$password" <<'PY'
import base64
import hashlib
import os
import sys

password = sys.argv[1].encode("utf-8")
salt = os.urandom(16)
dk = hashlib.pbkdf2_hmac("sha512", password, salt, 100000)
print(f'@ByteArray({base64.b64encode(salt).decode()}:{base64.b64encode(dk).decode()})')
PY
}

get_secret_key() {
  local key="$1"
  "${KUBECTL[@]}" -n "$NAMESPACE" get secret "$SECRET_NAME" \
    -o "jsonpath={.data.$key}" 2>/dev/null | base64 -d 2>/dev/null || true
}

patch_secret_keys() {
  local user="$1"
  local pass="$2"
  local stack_user="${3:-}"
  local stack_pass="${4:-}"
  local patch
  patch="{\"stringData\":{\"QBITTORRENT_USERNAME\":\"$user\",\"QBITTORRENT_PASSWORD\":\"$pass\""
  if [[ -n "$stack_user" ]]; then
    patch+=",\"STACK_ADMIN_USERNAME\":\"$stack_user\""
  fi
  if [[ -n "$stack_pass" ]]; then
    patch+=",\"STACK_ADMIN_PASSWORD\":\"$stack_pass\""
  fi
  patch+="}}"
  "${KUBECTL[@]}" -n "$NAMESPACE" patch secret "$SECRET_NAME" --type merge -p "$patch" >/dev/null
}

ensure_secret() {
  if ! "${KUBECTL[@]}" -n "$NAMESPACE" get secret "$SECRET_NAME" >/dev/null 2>&1; then
    cat <<EOF | "${KUBECTL[@]}" apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: $SECRET_NAME
  namespace: $NAMESPACE
type: Opaque
stringData:
  QBITTORRENT_USERNAME: "$DEFAULT_QBIT_USER"
  QBITTORRENT_PASSWORD: "$DEFAULT_QBIT_PASS"
  STACK_ADMIN_USERNAME: "$DEFAULT_STACK_ADMIN_USER"
  STACK_ADMIN_PASSWORD: "$DEFAULT_STACK_ADMIN_PASS"
  JELLYFIN_API_KEY: ""
  JELLYFIN_USER_ID: ""
  UNPACKERR_SONARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_RADARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_LIDARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_READARR_API_KEY: "replace-after-first-boot"
EOF
    echo "[OK] Created $NAMESPACE/$SECRET_NAME with default qBittorrent credentials."
  fi
}

extract_temp_password_from_logs() {
  local pod_name="${1:-}"
  if [[ -n "$pod_name" ]]; then
    "${KUBECTL[@]}" -n "$NAMESPACE" logs "$pod_name" --tail=300 2>/dev/null || true
  else
    {
      "${KUBECTL[@]}" -n "$NAMESPACE" logs "deploy/$QBIT_DEPLOYMENT" --tail=300 2>/dev/null || true
      "${KUBECTL[@]}" -n "$NAMESPACE" logs "deploy/$QBIT_DEPLOYMENT" --previous --tail=300 2>/dev/null || true
    }
  fi | awk '
    BEGIN { IGNORECASE=1 }
    /temporary password/ {
      line=$0
      sub(/^.*temporary password[^:]*:[[:space:]]*/, "", line)
      gsub(/\r/, "", line)
      if (length(line) > 0) { pass=line }
    }
    END {
      if (length(pass) > 0) { print pass }
    }
  '
}

wait_for_temp_password() {
  local pod_name="${1:-}"
  local max_wait="${2:-45}"
  local waited=0
  local found=""
  while (( waited < max_wait )); do
    found="$(extract_temp_password_from_logs "$pod_name")"
    if [[ -n "$found" ]]; then
      printf '%s\n' "$found"
      return 0
    fi
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

collect_qbit_usernames() {
  local out=()
  local discovered

  out+=("$QB_USER" "admin")

  discovered="$("${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$QBIT_DEPLOYMENT" -- sh -c '
find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null \
  | while read -r f; do
      awk -F= '\''/^WebUI\\Username=/{print $2}'\'' "$f" 2>/dev/null || true
    done
' 2>/dev/null || true)"

  if [[ -n "$discovered" ]]; then
    while IFS= read -r name; do
      [[ -n "$name" ]] && out+=("$name")
    done <<< "$discovered"
  fi

  printf '%s\n' "${out[@]}" | awk 'NF && !seen[$0]++'
}

qbit_login_any_user() {
  local base_url="$1"
  local pass="$2"
  local source_label="${3:-unknown}"
  local user
  QBIT_AUTH_USER=""
  for user in "${QBIT_USERS[@]}"; do
    if qbit_login "$base_url" "$user" "$pass"; then
      record_login_attempt "$source_label" "$user" "ok"
      QBIT_AUTH_USER="$user"
      return 0
    fi
    record_login_attempt "$source_label" "$user" "fail"
  done
  return 1
}

wait_for_local_qbit() {
  local base_url="$1"
  local waited=0
  local code="000"
  echo "[INFO] Waiting for qB WebUI on ${base_url} (timeout ${QBIT_WAIT_SECONDS}s)"
  while (( waited < QBIT_WAIT_SECONDS )); do
    if [[ -n "${PF_PID:-}" ]] && ! kill -0 "$PF_PID" >/dev/null 2>&1; then
      echo "[ERR] Port-forward process exited before qBittorrent became reachable." >&2
      return 1
    fi
    code="$(curl -sS -o /dev/null -w '%{http_code}' "$base_url/" 2>/dev/null || true)"
    if [[ "$code" =~ ^[0-9]{3}$ && "$code" != "000" ]]; then
      echo "[INFO] qB WebUI is reachable (HTTP $code)"
      return 0
    fi
    if (( waited == 0 || waited % 5 == 0 )); then
      echo "[WAIT] qB WebUI not ready yet (${waited}s/${QBIT_WAIT_SECONDS}s, last_http=${code})"
    fi
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

pick_ready_qbit_pod() {
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app="$QBIT_DEPLOYMENT" \
    --no-headers \
    -o custom-columns=NAME:.metadata.name,READY:.status.containerStatuses[0].ready,PHASE:.status.phase 2>/dev/null \
    | awk '$2=="true" && $3=="Running"{print $1; exit}'
}

cleanup_port_forward() {
  if [[ -n "${PF_PID:-}" ]] && kill -0 "$PF_PID" >/dev/null 2>&1; then
    kill "$PF_PID" >/dev/null 2>&1 || true
  fi
  PF_PID=""
}

start_qbit_api_connection() {
  READY_POD="$(pick_ready_qbit_pod || true)"
  if [[ -n "$QBIT_API_URL" ]]; then
    USING_PORT_FORWARD=0
    BASE_URL="${QBIT_API_URL%/}"
    echo "[INFO] Using direct qB API URL: $BASE_URL"
    return 0
  fi

  USING_PORT_FORWARD=1
  cleanup_port_forward
  : >"$PORT_FWD_LOG"

  PORT_FORWARD_TARGET="svc/$QBIT_DEPLOYMENT"
  if [[ -n "$READY_POD" ]]; then
    PORT_FORWARD_TARGET="pod/$READY_POD"
  fi

  echo "[INFO] Port-forward target: $PORT_FORWARD_TARGET"
  "${KUBECTL[@]}" -n "$NAMESPACE" port-forward "$PORT_FORWARD_TARGET" "${LOCAL_PORT}:8080" >"$PORT_FWD_LOG" 2>&1 &
  PF_PID="$!"
  BASE_URL="http://127.0.0.1:${LOCAL_PORT}"
}

locate_qbit_conf_path() {
  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$QBIT_DEPLOYMENT" -- sh -c '
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
}

force_reset_qbit_auth() {
  local conf_path
  conf_path="$(locate_qbit_conf_path)"
  if [[ -z "$conf_path" ]]; then
    echo "[WARN] Could not locate qBittorrent.conf to force-reset auth." >&2
    return 1
  fi

  echo "[WARN] Forcing qBittorrent WebUI auth reset at ${conf_path} (and any other qB config files under /config)"
  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$QBIT_DEPLOYMENT" -- sh -c "
set -e
for f in \$(find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null); do
  cp \"\$f\" \"\${f}.bak.$(date +%s)\" || true
  sed -i \
    -e '/^WebUI\\\\Username=/d' \
    -e '/^WebUI\\\\Password/d' \
    \"\$f\" || true
done
"

  echo "[INFO] Restarting deploy/$QBIT_DEPLOYMENT after auth reset"
  "${KUBECTL[@]}" -n "$NAMESPACE" rollout restart "deploy/$QBIT_DEPLOYMENT" >/dev/null
  if ! "${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/$QBIT_DEPLOYMENT" --timeout="$ROLL_OUT_TIMEOUT" >/dev/null; then
    echo "[WARN] deploy/$QBIT_DEPLOYMENT did not fully roll out in $ROLL_OUT_TIMEOUT after auth reset." >&2
  fi
  return 0
}

sync_qbit_auth_config() {
  local user="$1"
  local pass_hash="$2"
  local shell_payload
  shell_payload="$(cat <<'SH'
set -e
found=0
for f in $(find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null); do
  found=1
  cp "$f" "${f}.bak.$(date +%s)" || true
  sed -i \
    -e '/^WebUI\\Username=/d' \
    -e '/^WebUI\\Password_PBKDF2=/d' \
    -e '/^WebUI\\Password_ha1=/d' \
    "$f"
  {
    echo "WebUI\\Username=${QBIT_USER_ESC}"
    echo "WebUI\\Password_PBKDF2=${QBIT_HASH_ESC}"
  } >> "$f"
  echo "[INFO] Updated qB auth lines in $f"
done

if [ "$found" -eq 0 ]; then
  echo "[ERR] No qBittorrent.conf file found under /config" >&2
  exit 1
fi
SH
)"

  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$QBIT_DEPLOYMENT" -- \
    env "QBIT_USER_ESC=${user}" "QBIT_HASH_ESC=${pass_hash}" sh -c "$shell_payload"

  echo "[INFO] Restarting deploy/$QBIT_DEPLOYMENT after config sync"
  "${KUBECTL[@]}" -n "$NAMESPACE" rollout restart "deploy/$QBIT_DEPLOYMENT" >/dev/null
  if ! "${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/$QBIT_DEPLOYMENT" --timeout="$ROLL_OUT_TIMEOUT" >/dev/null; then
    echo "[WARN] deploy/$QBIT_DEPLOYMENT did not fully roll out in $ROLL_OUT_TIMEOUT after config sync." >&2
  fi
}

qbit_login() {
  local base_url="$1"
  local user="$2"
  local pass="$3"
  local response code
  response="$(curl -sS \
    -c "$COOKIE_FILE" -b "$COOKIE_FILE" \
    -H "Origin: ${base_url}" \
    -H "Referer: ${base_url}/" \
    -H "User-Agent: media-stack-bootstrap/1.0" \
    --data-urlencode "username=$user" \
    --data-urlencode "password=$pass" \
    -w $'\n%{http_code}' \
    "$base_url/api/v2/auth/login" 2>/dev/null || true)"
  code="${response##*$'\n'}"
  response="${response%$'\n'*}"
  QBIT_LAST_CODE="$code"
  QBIT_LAST_BODY="$response"
  [[ "$code" =~ ^2 ]] && [[ "$response" == "Ok."* ]]
}

qbit_login_in_pod() {
  local user="$1"
  local pass="$2"
  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$QBIT_DEPLOYMENT" -- \
    env "QB_USER=$user" "QB_PASS=$pass" sh -lc '
tmp_body="/tmp/qb-login-body.$$"
code="$(curl -sS \
  -o "$tmp_body" -w "%{http_code}" \
  -H "Origin: http://127.0.0.1:8080" \
  -H "Referer: http://127.0.0.1:8080/" \
  -H "User-Agent: media-stack-bootstrap/1.0" \
  --data-urlencode "username=$QB_USER" \
  --data-urlencode "password=$QB_PASS" \
  "http://127.0.0.1:8080/api/v2/auth/login" 2>/dev/null || true)"
body="$(cat "$tmp_body" 2>/dev/null || true)"
rm -f "$tmp_body" >/dev/null 2>&1 || true
case "$code" in
  2*) [ "$body" = "Ok." ] || [ "${body#Ok.}" != "$body" ] ;;
  *) false ;;
esac
' >/dev/null 2>&1
}

sanitize_login_body() {
  local body="${1:-}"
  body="${body//$'\r'/ }"
  body="${body//$'\n'/ }"
  if [[ -z "$body" ]]; then
    body="<empty>"
  fi
  printf '%s' "$body" | cut -c1-80
}

record_login_attempt() {
  local source_label="$1"
  local user="$2"
  local result="$3"
  local body_sanitized
  body_sanitized="$(sanitize_login_body "${QBIT_LAST_BODY:-}")"
  QBIT_LOGIN_ATTEMPTS+=("source=${source_label} user=${user} result=${result} code=${QBIT_LAST_CODE:-000} body='${body_sanitized}'")
  if [[ "$QBIT_LOG_VERBOSE" == "1" ]]; then
    echo "[INFO] qB login attempt: source=${source_label} user=${user} result=${result} code=${QBIT_LAST_CODE:-000} body='${body_sanitized}'"
  fi
}

print_login_attempt_summary() {
  local row
  if [[ "${#QBIT_LOGIN_ATTEMPTS[@]}" -eq 0 ]]; then
    echo "[INFO] qB login attempts: none recorded"
    return 0
  fi
  echo "[INFO] qB login attempt summary:"
  for row in "${QBIT_LOGIN_ATTEMPTS[@]}"; do
    echo "  - $row"
  done
}

dump_qbit_auth_diagnostics() {
  echo "[INFO] qB auth diagnostics begin"
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app="$QBIT_DEPLOYMENT" -o wide 2>/dev/null || true

  echo "[INFO] qB startup log hints (masked):"
  "${KUBECTL[@]}" -n "$NAMESPACE" logs "deploy/$QBIT_DEPLOYMENT" --tail=250 2>/dev/null \
    | awk 'BEGIN{IGNORECASE=1} /administrator username|temporary password|WebUI/{print}' \
    | sed -E 's/(temporary password[^:]*:).*/\1 <redacted>/I' \
    || true

  echo "[INFO] qB config auth lines (password values redacted):"
  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$QBIT_DEPLOYMENT" -- sh -c '
for f in $(find /config -maxdepth 6 -name qBittorrent.conf 2>/dev/null); do
  echo "FILE: $f"
  grep -nE "^WebUI\\(Username|Password)" "$f" 2>/dev/null \
    | sed -E "s/(Password[^=]*=).*/\1<redacted>/" || true
done
' 2>/dev/null || true
  echo "[INFO] qB auth diagnostics end"
}

qbit_set_webui_credentials() {
  local base_url="$1"
  local user="$2"
  local pass="$3"
  local json code
  json="$(printf '{"web_ui_username":"%s","web_ui_password":"%s"}' \
    "$(json_escape "$user")" "$(json_escape "$pass")")"
  code="$(curl -sS \
    -c "$COOKIE_FILE" -b "$COOKIE_FILE" \
    -H "Origin: ${base_url}" \
    -H "Referer: ${base_url}/" \
    -H "User-Agent: media-stack-bootstrap/1.0" \
    --data-urlencode "json=$json" \
    -o /dev/null -w '%{http_code}' \
    "$base_url/api/v2/app/setPreferences" 2>/dev/null || true)"
  if [[ ! "$code" =~ ^2 ]]; then
    echo "[ERR] qBittorrent setPreferences failed (HTTP ${code:-000})" >&2
    return 1
  fi
}

qbit_set_webui_credentials_in_pod() {
  local auth_user="$1"
  local auth_pass="$2"
  local target_user="$3"
  local target_pass="$4"

  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$QBIT_DEPLOYMENT" -- \
    env AUTH_USER="$auth_user" AUTH_PASS="$auth_pass" TARGET_USER="$target_user" TARGET_PASS="$target_pass" \
    sh -lc '
set -e
cookie="/tmp/qb-cookie.$$"
login_body="/tmp/qb-login.$$"
prefs_body="/tmp/qb-prefs.$$"

login_code="$(curl -sS \
  -c "$cookie" -b "$cookie" \
  --data-urlencode "username=$AUTH_USER" \
  --data-urlencode "password=$AUTH_PASS" \
  -o "$login_body" -w "%{http_code}" \
  "http://127.0.0.1:8080/api/v2/auth/login" 2>/dev/null || true)"
login_text="$(cat "$login_body" 2>/dev/null || true)"

if [ "${login_code#2}" = "$login_code" ]; then
  rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
  exit 12
fi
case "$login_text" in
  Ok.*) ;;
  *)
    rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
    exit 13
    ;;
esac

prefs_json="$(printf "{\"web_ui_username\":\"%s\",\"web_ui_password\":\"%s\"}" "$TARGET_USER" "$TARGET_PASS")"
prefs_code="$(curl -sS \
  -c "$cookie" -b "$cookie" \
  --data-urlencode "json=$prefs_json" \
  -o "$prefs_body" -w "%{http_code}" \
  "http://127.0.0.1:8080/api/v2/app/setPreferences" 2>/dev/null || true)"

if [ "${prefs_code#2}" = "$prefs_code" ]; then
  rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
  exit 14
fi

rm -f "$cookie" "$login_body" "$prefs_body" >/dev/null 2>&1 || true
'
}

try_inpod_reconcile_with_auth() {
  local source_label="$1"
  local auth_user="$2"
  local auth_pass="$3"

  if ! qbit_set_webui_credentials_in_pod "$auth_user" "$auth_pass" "$QB_USER" "$QB_PASS"; then
    return 1
  fi

  if qbit_login_in_pod "$QB_USER" "$QB_PASS"; then
    echo "[OK] qBittorrent WebUI credentials reconciled from ${source_label} via in-pod API."
    return 0
  fi

  return 1
}

ensure_secret
if [[ "$QBIT_FORCE_CONFIG_SYNC" == "1" ]]; then
  require_python3
fi

QB_USER="$(get_secret_key QBITTORRENT_USERNAME)"
QB_PASS="$(get_secret_key QBITTORRENT_PASSWORD)"
STACK_ADMIN_USER="$(get_secret_key STACK_ADMIN_USERNAME)"
STACK_ADMIN_PASS="$(get_secret_key STACK_ADMIN_PASSWORD)"

if [[ -z "$STACK_ADMIN_USER" ]]; then
  STACK_ADMIN_USER="$DEFAULT_STACK_ADMIN_USER"
fi
if [[ -z "$STACK_ADMIN_PASS" || "$STACK_ADMIN_PASS" == "change-me" ]]; then
  STACK_ADMIN_PASS="$DEFAULT_STACK_ADMIN_PASS"
fi

if [[ -z "$QB_USER" ]]; then
  QB_USER="$DEFAULT_QBIT_USER"
fi

if [[ -z "$QB_PASS" || "$QB_PASS" == "change-me" ]]; then
  QB_PASS="$DEFAULT_QBIT_PASS"
fi

if [[ "$QBIT_USE_STACK_ADMIN" == "1" ]]; then
  QB_USER="$STACK_ADMIN_USER"
  QB_PASS="$STACK_ADMIN_PASS"
fi

patch_secret_keys "$QB_USER" "$QB_PASS" "$STACK_ADMIN_USER" "$STACK_ADMIN_PASS"
echo "[OK] Secret $NAMESPACE/$SECRET_NAME now has qBittorrent credentials for user '$QB_USER'."
echo "[INFO] Target qB credentials from secret: username='$QB_USER', password_length=${#QB_PASS}"

if qbit_login_in_pod "$QB_USER" "$QB_PASS"; then
  echo "[OK] qBittorrent credentials validated from inside the qB pod."
  exit 0
fi
echo "[WARN] In-pod qB credential check failed; continuing with recovery flow."

if try_inpod_reconcile_with_auth "admin/adminadmin fallback" "admin" "adminadmin"; then
  exit 0
fi

if [[ -n "${STACK_ADMIN_PASS:-}" ]]; then
  stack_user="${STACK_ADMIN_USER:-$QB_USER}"
  if try_inpod_reconcile_with_auth "stack-admin fallback credentials" "$stack_user" "$STACK_ADMIN_PASS"; then
    exit 0
  fi
fi

TEMP_PASS="$(wait_for_temp_password "" 30 || true)"
if [[ -n "$TEMP_PASS" ]] && try_inpod_reconcile_with_auth "temporary startup password" "admin" "$TEMP_PASS"; then
  exit 0
fi

if [[ "$FORCE_RESET_ON_AUTH_FAILURE" == "1" ]]; then
  echo "[WARN] In-pod fallback auth did not work; forcing qB auth reset and retrying in-pod reconcile once."
  if force_reset_qbit_auth; then
    RESET_POD="$(pick_ready_qbit_pod || true)"
    if [[ -n "$RESET_POD" ]]; then
      echo "[INFO] Looking for temporary startup password in pod logs: $RESET_POD"
    fi
    TEMP_PASS="$(wait_for_temp_password "$RESET_POD" 90 || true)"
    if [[ -z "$TEMP_PASS" ]]; then
      TEMP_PASS="$(wait_for_temp_password "" 30 || true)"
    fi
    if [[ -n "$TEMP_PASS" ]] && try_inpod_reconcile_with_auth "temporary password after reset" "admin" "$TEMP_PASS"; then
      exit 0
    fi
  fi
fi

CONFIG_SYNC_DONE=0
if [[ "$QBIT_FORCE_CONFIG_SYNC" == "1" ]]; then
  echo "[INFO] qB deterministic credential sync enabled: writing PBKDF2 hash to qB config."
  QBIT_HASH="$(generate_qbit_pbkdf2_hash "$QB_PASS")"
  sync_qbit_auth_config "$QB_USER" "$QBIT_HASH"
  CONFIG_SYNC_DONE=1
  echo "[OK] qBittorrent auth synced in config to match Kubernetes secret."
fi

if [[ "$QBIT_API_VALIDATION" != "1" && "$QBIT_STRICT_LOGIN_CHECK" != "1" && "$CONFIG_SYNC_DONE" == "1" ]]; then
  if qbit_login_in_pod "$QB_USER" "$QB_PASS"; then
    echo "[INFO] qB API validation disabled (QBIT_API_VALIDATION=0); relying on deterministic config sync."
    echo "[OK] qBittorrent credentials have been applied from secret via config-as-code."
    exit 0
  fi
  echo "[WARN] Deterministic config sync completed but in-pod login still failed; continuing with recovery flow." >&2
fi

if ! "${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/$QBIT_DEPLOYMENT" --timeout="$ROLL_OUT_TIMEOUT" >/dev/null; then
  echo "[WARN] qBittorrent deployment rollout not fully healthy within ${ROLL_OUT_TIMEOUT}; continuing with ready-pod fallback." >&2
fi

PORT_FWD_LOG="$(mktemp)"
COOKIE_FILE="$(mktemp)"
PF_PID=""
USING_PORT_FORWARD=0
QBIT_LAST_CODE=""
QBIT_LAST_BODY=""
QBIT_AUTH_USER=""
declare -a QBIT_LOGIN_ATTEMPTS=()
cleanup() {
  cleanup_port_forward
  rm -f "$PORT_FWD_LOG" "$COOKIE_FILE"
}
trap cleanup EXIT

start_qbit_api_connection

if ! wait_for_local_qbit "$BASE_URL"; then
  echo "[ERR] Could not reach qBittorrent API at ${BASE_URL}" >&2
  if [[ "$USING_PORT_FORWARD" == "1" ]]; then
    echo "[ERR] port-forward logs:" >&2
    cat "$PORT_FWD_LOG" >&2 || true
  fi
  exit 1
fi

mapfile -t QBIT_USERS < <(collect_qbit_usernames)
echo "[INFO] qB username candidates: ${QBIT_USERS[*]}"

if qbit_login_any_user "$BASE_URL" "$QB_PASS" "secret-password"; then
  if [[ "$QBIT_AUTH_USER" == "$QB_USER" ]]; then
    echo "[OK] qBittorrent credentials already match the secret."
    exit 0
  fi
  echo "[INFO] qBittorrent password matches secret but username differs ('${QBIT_AUTH_USER}' vs '${QB_USER}'); reconciling to secret values."
  qbit_set_webui_credentials "$BASE_URL" "$QB_USER" "$QB_PASS"
  reconciled=1
else
  reconciled=0
fi

echo "[INFO] qBittorrent credentials differ from secret; attempting one-time reconciliation."

if [[ "$reconciled" != "1" ]] && qbit_login_any_user "$BASE_URL" "adminadmin" "adminadmin-fallback"; then
  echo "[INFO] qBittorrent login succeeded with fallback password (user='${QBIT_AUTH_USER}')."
  qbit_set_webui_credentials "$BASE_URL" "$QB_USER" "$QB_PASS"
  reconciled=1
fi

if [[ "$reconciled" != "1" ]]; then
  TEMP_PASS="$(wait_for_temp_password "$READY_POD" 15 || true)"
  if [[ -z "$TEMP_PASS" ]]; then
    TEMP_PASS="$(wait_for_temp_password "" 15 || true)"
  fi
  if [[ -n "$TEMP_PASS" ]] && qbit_login_any_user "$BASE_URL" "$TEMP_PASS" "temp-password"; then
    echo "[INFO] qBittorrent login succeeded using temporary startup password from logs (user='${QBIT_AUTH_USER}')."
    qbit_set_webui_credentials "$BASE_URL" "$QB_USER" "$QB_PASS"
    reconciled=1
  fi
fi

if [[ "$reconciled" != "1" && "$FORCE_RESET_ON_AUTH_FAILURE" == "1" ]]; then
  echo "[WARN] Could not authenticate with known fallbacks; forcing qB auth reset and retrying once."
  if force_reset_qbit_auth; then
    start_qbit_api_connection
    if wait_for_local_qbit "$BASE_URL"; then
      mapfile -t QBIT_USERS < <(collect_qbit_usernames)
      echo "[INFO] qB username candidates after reset: ${QBIT_USERS[*]}"
      TEMP_PASS="$(wait_for_temp_password "$READY_POD" 45 || true)"
      if [[ -z "$TEMP_PASS" ]]; then
        TEMP_PASS="$(wait_for_temp_password "" 45 || true)"
      fi
      if [[ -n "$TEMP_PASS" ]] && qbit_login_any_user "$BASE_URL" "$TEMP_PASS" "temp-password-after-reset"; then
        echo "[INFO] qBittorrent login succeeded after forced reset using temporary password (user='${QBIT_AUTH_USER}')."
        qbit_set_webui_credentials "$BASE_URL" "$QB_USER" "$QB_PASS"
        reconciled=1
      fi
    fi
  fi
fi

if [[ "$reconciled" != "1" ]]; then
  if qbit_login_in_pod "$QB_USER" "$QB_PASS"; then
    echo "[WARN] External qB API validation failed, but in-pod qB auth succeeded with secret credentials."
    echo "[OK] qBittorrent credentials are valid and persisted."
    exit 0
  fi
  print_login_attempt_summary
  dump_qbit_auth_diagnostics
  if [[ "$CONFIG_SYNC_DONE" == "1" && "$QBIT_STRICT_LOGIN_CHECK" != "1" ]]; then
    echo "[WARN] qB API validation still failed, but config sync has been applied." >&2
    echo "[WARN] Continuing non-strict mode; downstream bootstrap will verify qB connectivity from inside cluster." >&2
    exit 0
  fi
  echo "[ERR] Could not authenticate to qBittorrent with secret creds, admin/adminadmin, temporary startup password, or forced auth reset." >&2
  echo "[ERR] Manual recovery:" >&2
  echo "      bash scripts/reset-qbit-webui-auth.sh" >&2
  echo '      bash scripts/set-qbit-secret.sh <USERNAME> <PASSWORD>' >&2
  exit 1
fi

sleep 1
if qbit_login_in_pod "$QB_USER" "$QB_PASS"; then
  echo "[OK] qBittorrent WebUI credentials reconciled to secret values."
  exit 0
fi

: >"$COOKIE_FILE"
if qbit_login "$BASE_URL" "$QB_USER" "$QB_PASS"; then
  echo "[OK] qBittorrent WebUI credentials reconciled to secret values."
  exit 0
fi

echo "[ERR] qBittorrent credential update did not validate. Check qBittorrent logs and rerun." >&2
exit 1
