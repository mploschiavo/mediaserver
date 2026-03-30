#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
TIMEOUT="${TIMEOUT:-10m}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib/bootstrap-script-configmap.sh"
CONFIG_FILE="${1:-$ROOT_DIR/bootstrap/media-stack.bootstrap.json}"
HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-15}"
JOB_LOG_TAIL_LINES="${JOB_LOG_TAIL_LINES:-120}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
PREPARE_HOST_ROOT="${PREPARE_HOST_ROOT:-/srv/media-stack}"
INGRESS_NAME="${INGRESS_NAME:-media-stack-ingress}"
HB_PID=""
JOB_LOG_FILE="$(mktemp -t media-stack-bootstrap-log.XXXXXX)"
JOB_CONFIG_FILE="$(mktemp -t media-stack-bootstrap-config.XXXXXX)"
RUN_START_EPOCH="$(date +%s)"
CURRENT_PHASE=""
CURRENT_PHASE_START=0
declare -a PHASE_NAMES=()
declare -a PHASE_RESULTS=()
declare -a PHASE_SECONDS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/run-bootstrap-job.sh [CONFIG_FILE]

Description:
  Runs the media-stack bootstrap Kubernetes Job.
  - ensures qBittorrent credentials secret exists and is usable by automation
  - ensures SABnzbd API-access guardrails (host whitelist/local ranges) are compatible with Arr
  - creates/updates ConfigMaps from local bootstrap config + script
  - recreates the Job
  - waits for completion and prints logs

Environment variables:
  NAMESPACE   (default: media-stack)
  TIMEOUT     (default: 10m)
  HEARTBEAT_INTERVAL (default: 15)
  JOB_LOG_TAIL_LINES (default: 120)
  AUTO_INDEXER_LOG_SKIPS (default: 0; set 1 for per-template skip logs)
  PREPARE_HOST_ROOT (default: /srv/media-stack)
  INGRESS_NAME (default: media-stack-ingress)
  SKIP_QBIT_ENSURE (default: 0)
  SKIP_SAB_ENSURE (default: 0)
  ALERT_WEBHOOK_URL (optional; POSTs JSON status updates)
EOF
}

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

ensure_bootstrap_pvc_prereqs() {
  local storage_manifest="$ROOT_DIR/k8s/storage-pvc.yaml"
  local -a required_pvcs=(
    media-stack-config-jellyfin
    media-stack-config-jellyseerr
    media-stack-config-sonarr
    media-stack-config-radarr
    media-stack-config-lidarr
    media-stack-config-readarr
    media-stack-config-bazarr
    media-stack-config-prowlarr
    media-stack-config-sabnzbd
    media-stack-config-homepage
    media-stack-config-maintainerr
    media-stack-config-jellyfin-auto-collections
    media-stack-data-torrents
    media-stack-data-usenet
    media-stack-media
  )
  local -a missing=()
  local pvc

  if [[ -f "$storage_manifest" ]]; then
    info "Ensuring bootstrap PVC prerequisites via $storage_manifest"
    manifest_overrides <"$storage_manifest" | "${KUBECTL[@]}" apply -f -
  else
    warn "PVC manifest not found at $storage_manifest"
  fi

  for pvc in "${required_pvcs[@]}"; do
    if ! "${KUBECTL[@]}" -n "$NAMESPACE" get pvc "$pvc" >/dev/null 2>&1; then
      missing+=("$pvc")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    warn "Missing required PVC(s) for bootstrap job: ${missing[*]}"
    warn "Apply storage PVCs and retry: ${KUBECTL[*]} apply -f $ROOT_DIR/k8s/storage-pvc.yaml"
    return 1
  fi

  info "Bootstrap PVC prerequisites are present."
  return 0
}

read_api_key_from_deploy() {
  local app="$1"
  "${KUBECTL[@]}" -n "$NAMESPACE" exec "deploy/$app" -- sh -c \
    "sed -n 's:.*<ApiKey>\\(.*\\)</ApiKey>.*:\\1:p' /config/config.xml | head -n1" 2>/dev/null \
    | tr -d '\r\n'
}

read_sab_api_key_from_deploy() {
  "${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/sabnzbd -- sh -c \
    "sed -n 's/^[[:space:]]*api_key[[:space:]]*=[[:space:]]*//p' /config/sabnzbd.ini | head -n1" 2>/dev/null \
    | tr -d '\r\n'
}

patch_secret_api_key() {
  local key_name="$1"
  local key_value="$2"
  [[ -z "$key_name" || -z "$key_value" ]] && return 0

  local payload
  payload="$(python3 - "$key_name" "$key_value" <<'PY'
import json
import sys
key = sys.argv[1]
val = sys.argv[2]
print(json.dumps({"stringData": {key: val}}))
PY
)"
  "${KUBECTL[@]}" -n "$NAMESPACE" patch secret media-stack-secrets --type merge -p "$payload" >/dev/null
}

prime_servarr_api_keys_secret() {
  local -a apps=(sonarr radarr lidarr readarr prowlarr)
  local found=0
  local app key upper

  if ! "${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets >/dev/null 2>&1; then
    warn "Secret ${NAMESPACE}/media-stack-secrets not found; skipping Arr API key priming."
    return 0
  fi

  for app in "${apps[@]}"; do
    key="$(read_api_key_from_deploy "$app" || true)"
    if [[ -z "$key" ]]; then
      warn "Could not read API key from deploy/$app yet; continuing."
      continue
    fi

    upper="$(printf '%s' "$app" | tr '[:lower:]' '[:upper:]')"
    patch_secret_api_key "${upper}_API_KEY" "$key"
    if [[ "$app" != "prowlarr" ]]; then
      patch_secret_api_key "UNPACKERR_${upper}_API_KEY" "$key"
    fi
    info "Seeded ${upper}_API_KEY in media-stack-secrets from deploy/$app"
    found=$((found + 1))
  done

  if (( found == 0 )); then
    warn "No Arr/Prowlarr API keys were discovered from running deployments."
  else
    info "Primed API keys in secret for ${found} app(s)."
  fi
  return 0
}

prime_sab_api_key_secret() {
  local key="${SABNZBD_API_KEY:-}"

  if ! "${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets >/dev/null 2>&1; then
    warn "Secret ${NAMESPACE}/media-stack-secrets not found; skipping SABnzbd API key priming."
    return 0
  fi

  if [[ -z "$key" ]]; then
    key="$(read_sab_api_key_from_deploy || true)"
  fi

  if [[ -z "$key" ]]; then
    warn "Could not discover SABnzbd API key from env or deploy/sabnzbd; continuing."
    return 0
  fi

  patch_secret_api_key "SABNZBD_API_KEY" "$key"
  info "Seeded SABNZBD_API_KEY in media-stack-secrets."
  return 0
}

notify() {
  local status="$1"
  local message="$2"
  [[ -z "$ALERT_WEBHOOK_URL" ]] && return 0
  curl -fsS -X POST \
    -H "Content-Type: application/json" \
    --data "{\"status\":\"$status\",\"message\":\"$message\"}" \
    "$ALERT_WEBHOOK_URL" >/dev/null || true
}

cleanup() {
  if [[ -n "$HB_PID" ]] && kill -0 "$HB_PID" >/dev/null 2>&1; then
    kill "$HB_PID" >/dev/null 2>&1 || true
  fi
  [[ -n "${JOB_LOG_FILE:-}" && -f "$JOB_LOG_FILE" ]] && rm -f "$JOB_LOG_FILE" || true
  [[ -n "${JOB_CONFIG_FILE:-}" && -f "$JOB_CONFIG_FILE" ]] && rm -f "$JOB_CONFIG_FILE" || true
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
  warn "Bootstrap job runner failed at line ${line} while running: ${cmd}"
  cleanup
  print_phase_summary
  notify "error" "media-stack bootstrap job failed (namespace=$NAMESPACE)"
  exit "$code"
}

trap cleanup EXIT INT TERM
trap 'on_error $LINENO "$BASH_COMMAND"' ERR

start_job_heartbeat() {
  local job_name="$1"
  local selector="$2"
  (
    local elapsed=0
    local last_pending_dump=-99999
    while true; do
      info "Waiting on job/$job_name (elapsed ${elapsed}s, timeout ${TIMEOUT})"
      "${KUBECTL[@]}" -n "$NAMESPACE" get job "$job_name" \
        -o custom-columns=NAME:.metadata.name,COMPLETIONS:.status.succeeded,FAILED:.status.failed,ACTIVE:.status.active,AGE:.metadata.creationTimestamp \
        --no-headers 2>/dev/null || true
      "${KUBECTL[@]}" -n "$NAMESPACE" get pods -l "$selector" \
        -o custom-columns=NAME:.metadata.name,PHASE:.status.phase,READY:.status.containerStatuses[0].ready,RESTARTS:.status.containerStatuses[0].restartCount \
        --no-headers 2>/dev/null || true

      local pod_name
      pod_name="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l "$selector" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
      if [[ -n "$pod_name" ]]; then
        local pod_phase
        pod_phase="$("${KUBECTL[@]}" -n "$NAMESPACE" get pod "$pod_name" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
        if [[ "$pod_phase" == "Pending" ]]; then
          if (( elapsed - last_pending_dump >= 45 )); then
            local sched_reason sched_message
            sched_reason="$("${KUBECTL[@]}" -n "$NAMESPACE" get pod "$pod_name" -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].reason}' 2>/dev/null || true)"
            sched_message="$("${KUBECTL[@]}" -n "$NAMESPACE" get pod "$pod_name" -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].message}' 2>/dev/null || true)"
            warn "Job pod is Pending: ${pod_name} (reason=${sched_reason:-unknown})"
            if [[ -n "$sched_message" ]]; then
              warn "Job pod scheduling message: $sched_message"
            fi
            "${KUBECTL[@]}" -n "$NAMESPACE" describe pod "$pod_name" 2>/dev/null \
              | sed -n '/Events:/,$p' \
              | sed -n '1,16p' \
              | sed 's/^/[PENDING] /' || true
            last_pending_dump=$elapsed
          fi
        else
          "${KUBECTL[@]}" -n "$NAMESPACE" logs "$pod_name" --tail=8 2>/dev/null | sed 's/^/[JOB] /' || true
        fi
      fi

      sleep "$HEARTBEAT_INTERVAL"
      elapsed=$((elapsed + HEARTBEAT_INTERVAL))
    done
  ) &
  HB_PID="$!"
}

parse_timeout_seconds() {
  local raw="${1:-10m}"
  local num unit
  if [[ "$raw" =~ ^([0-9]+)([smh]?)$ ]]; then
    num="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    case "$unit" in
      h) echo $((num * 3600)) ;;
      m|"") echo $((num * 60)) ;;
      s) echo "$num" ;;
      *) echo 600 ;;
    esac
    return 0
  fi
  echo 600
}

wait_for_job_complete_or_fail() {
  local job_name="$1"
  local timeout_seconds="$2"
  local start elapsed
  local succeeded failed pod_phase failed_condition failed_pods complete_condition backoff_condition
  local pod_name pod_sched_message

  start="$(date +%s)"
  while true; do
    if ! "${KUBECTL[@]}" -n "$NAMESPACE" get job "$job_name" >/dev/null 2>&1; then
      warn "Job ${NAMESPACE}/${job_name} not found while waiting."
      return 1
    fi

    succeeded="$("${KUBECTL[@]}" -n "$NAMESPACE" get job "$job_name" -o jsonpath='{.status.succeeded}' 2>/dev/null || true)"
    failed="$("${KUBECTL[@]}" -n "$NAMESPACE" get job "$job_name" -o jsonpath='{.status.failed}' 2>/dev/null || true)"
    failed_condition="$("${KUBECTL[@]}" -n "$NAMESPACE" get job "$job_name" -o jsonpath='{.status.conditions[?(@.type=="Failed")].status}' 2>/dev/null || true)"
    complete_condition="$("${KUBECTL[@]}" -n "$NAMESPACE" get job "$job_name" -o jsonpath='{.status.conditions[?(@.type=="Complete")].status}' 2>/dev/null || true)"
    backoff_condition="$("${KUBECTL[@]}" -n "$NAMESPACE" get job "$job_name" -o jsonpath='{.status.conditions[?(@.reason=="BackoffLimitExceeded")].status}' 2>/dev/null || true)"

    succeeded="$(printf '%s' "${succeeded:-0}" | tr -cd '0-9')"
    failed="$(printf '%s' "${failed:-0}" | tr -cd '0-9')"
    succeeded="${succeeded:-0}"
    failed="${failed:-0}"

    if [[ "$complete_condition" == "True" ]]; then
      return 0
    fi
    if [[ "$succeeded" =~ ^[0-9]+$ ]] && (( succeeded >= 1 )); then
      return 0
    fi
    if [[ "$failed_condition" == "True" ]]; then
      return 1
    fi
    if [[ "$backoff_condition" == "True" ]]; then
      return 1
    fi
    if [[ "$failed" =~ ^[0-9]+$ ]] && (( failed >= 1 )); then
      return 1
    fi

    # Fail fast on pod phase so we don't wait for delayed Job status propagation.
    pod_name="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l job-name="$job_name" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    pod_phase="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l job-name="$job_name" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)"
    if [[ "$pod_phase" == "Failed" || "$pod_phase" == "Unknown" ]]; then
      return 1
    fi
    if [[ "$pod_phase" == "Pending" && -n "$pod_name" ]]; then
      pod_sched_message="$("${KUBECTL[@]}" -n "$NAMESPACE" get pod "$pod_name" -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].message}' 2>/dev/null || true)"
      elapsed=$(( $(date +%s) - start ))
      if (( elapsed >= 20 )) && [[ "$pod_sched_message" =~ persistentvolumeclaim.*not\ found ]]; then
        warn "Job pod remained Pending because required PVCs are missing."
        warn "Scheduling message: ${pod_sched_message}"
        return 1
      fi
      if (( elapsed >= 120 )) && [[ "$pod_sched_message" =~ persistentvolumeclaim|unbound\ immediate\ PersistentVolumeClaims|volume\ node\ affinity\ conflict|Multi-Attach|didn\'t\ match\ Pod\'s\ node\ affinity ]]; then
        warn "Job pod remained Pending with a hard scheduling/storage error for ${elapsed}s."
        warn "Scheduling message: ${pod_sched_message}"
        return 1
      fi
    fi
    failed_pods="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l job-name="$job_name" --field-selector=status.phase=Failed -o jsonpath='{range .items[*]}x {end}' 2>/dev/null | wc -w | tr -d ' ' || true)"
    if [[ "$failed_pods" =~ ^[0-9]+$ ]] && (( failed_pods >= 1 )); then
      return 1
    fi

    elapsed=$(( $(date +%s) - start ))
    if (( elapsed >= timeout_seconds )); then
      return 2
    fi

    sleep 2
  done
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

info "Namespace: $NAMESPACE"
info "Config: $CONFIG_FILE"
info "Ingress: $INGRESS_NAME"
info "Heartbeat interval: ${HEARTBEAT_INTERVAL}s"
notify "info" "media-stack bootstrap job started (namespace=$NAMESPACE)"

if [[ "${SKIP_QBIT_ENSURE:-0}" != "1" ]]; then
  phase_start "Ensure qBittorrent credentials"
  info "Ensuring qBittorrent credentials are config-as-code and usable"
  bash "$ROOT_DIR/scripts/ensure-qbit-credentials.sh"
  phase_end "ok"
else
  phase_start "Ensure qBittorrent credentials"
  phase_end "skipped"
fi

if [[ "${SKIP_SAB_ENSURE:-0}" != "1" ]]; then
  phase_start "Ensure SABnzbd API access"
  info "Ensuring SABnzbd API access is reachable for Arr clients"
  NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/ensure-sabnzbd-api-access.sh"
  phase_end "ok"
else
  phase_start "Ensure SABnzbd API access"
  phase_end "skipped"
fi

phase_start "Resolve bootstrap config"
hosts_raw="$("${KUBECTL[@]}" -n "$NAMESPACE" get ingress "$INGRESS_NAME" -o jsonpath='{range .spec.rules[*]}{.host}{"\n"}{end}' 2>/dev/null || true)"
hosts_csv="$(printf '%s\n' "$hosts_raw" | sed '/^$/d' | sort -u | paste -sd, - || true)"
if [[ -n "$hosts_csv" ]]; then
  info "Injecting homepage hosts from ingress/$INGRESS_NAME: $hosts_csv"
else
  info "No ingress hosts discovered from ingress/$INGRESS_NAME; using bootstrap config defaults."
fi
python3 - "$CONFIG_FILE" "$JOB_CONFIG_FILE" "$hosts_csv" <<'PY'
import json
import sys

src = sys.argv[1]
dst = sys.argv[2]
hosts_csv = sys.argv[3]

with open(src, "r", encoding="utf-8") as fh:
    cfg = json.load(fh)

if hosts_csv.strip():
    hosts = [h.strip().lower() for h in hosts_csv.split(",") if h.strip()]
    homepage = cfg.setdefault("homepage", {})
    homepage["enabled"] = True
    homepage["hosts"] = hosts

with open(dst, "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, ensure_ascii=True, indent=2)
    fh.write("\n")
PY
info "Resolved job config: $JOB_CONFIG_FILE"
phase_end "ok"

phase_start "Ensure bootstrap PVC prerequisites"
ensure_bootstrap_pvc_prereqs
phase_end "ok"

phase_start "Prime Arr API keys into secret"
prime_servarr_api_keys_secret
phase_end "ok"

phase_start "Prime SAB API key into secret"
prime_sab_api_key_secret
phase_end "ok"

phase_start "Update bootstrap ConfigMaps"
info "Updating bootstrap script/config ConfigMaps"
bootstrap_script_cm_yaml="$(mktemp -t media-stack-bootstrap-script.XXXXXX.yaml)"
bootstrap_config_cm_yaml="$(mktemp -t media-stack-bootstrap-config.XXXXXX.yaml)"

bootstrap_script_configmap_create_yaml "$NAMESPACE" "$ROOT_DIR" "$bootstrap_script_cm_yaml"
if ! "${KUBECTL[@]}" -n "$NAMESPACE" replace -f "$bootstrap_script_cm_yaml" >/dev/null 2>&1; then
  "${KUBECTL[@]}" -n "$NAMESPACE" create -f "$bootstrap_script_cm_yaml" >/dev/null
else
  info "configmap/media-stack-bootstrap-script replaced"
fi

"${KUBECTL[@]}" -n "$NAMESPACE" create configmap media-stack-bootstrap-config \
  --from-file=config.json="$JOB_CONFIG_FILE" \
  --dry-run=client -o yaml >"$bootstrap_config_cm_yaml"
if ! "${KUBECTL[@]}" -n "$NAMESPACE" replace -f "$bootstrap_config_cm_yaml" >/dev/null 2>&1; then
  "${KUBECTL[@]}" -n "$NAMESPACE" create -f "$bootstrap_config_cm_yaml" >/dev/null
else
  info "configmap/media-stack-bootstrap-config replaced"
fi
rm -f "$bootstrap_script_cm_yaml" "$bootstrap_config_cm_yaml"
phase_end "ok"

phase_start "Recreate bootstrap Job"
info "Recreating bootstrap Job"
"${KUBECTL[@]}" -n "$NAMESPACE" delete job media-stack-bootstrap --ignore-not-found
manifest_overrides <"$ROOT_DIR/k8s/bootstrap-job.yaml" | "${KUBECTL[@]}" -n "$NAMESPACE" apply -f -
phase_end "ok"

phase_start "Wait for bootstrap Job completion"
start_job_heartbeat "media-stack-bootstrap" "app=media-stack-bootstrap"
TIMEOUT_SECONDS="$(parse_timeout_seconds "$TIMEOUT")"
wait_for_job_complete_or_fail "media-stack-bootstrap" "$TIMEOUT_SECONDS" || job_wait_rc="$?"
job_wait_rc="${job_wait_rc:-0}"
if [[ "$job_wait_rc" != "0" ]]; then
  if [[ "$job_wait_rc" == "1" ]]; then
    warn "Job failed before completion."
  else
    warn "Job did not complete within $TIMEOUT."
  fi
  "${KUBECTL[@]}" -n "$NAMESPACE" describe job media-stack-bootstrap || true
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=media-stack-bootstrap -o wide || true
  pod_name="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=media-stack-bootstrap -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [[ -n "$pod_name" ]]; then
    "${KUBECTL[@]}" -n "$NAMESPACE" describe pod "$pod_name" || true
  fi
  "${KUBECTL[@]}" -n "$NAMESPACE" logs job/media-stack-bootstrap --tail=300 --timestamps || true
  false
fi
phase_end "ok"

cleanup
phase_start "Print bootstrap Job logs"
"${KUBECTL[@]}" -n "$NAMESPACE" logs job/media-stack-bootstrap --timestamps > "$JOB_LOG_FILE"
tail -n "$JOB_LOG_TAIL_LINES" "$JOB_LOG_FILE"
phase_end "ok"

if grep -q "Jellyseerr: settings file bootstrap applied" "$JOB_LOG_FILE"; then
  phase_start "Restart Jellyseerr after file bootstrap"
  info "Jellyseerr settings were applied via file fallback; restarting deployment to load updated settings."
  "${KUBECTL[@]}" -n "$NAMESPACE" rollout restart deployment/jellyseerr
  "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deployment/jellyseerr --timeout=180s
  phase_end "ok"
fi

if grep -q "Homepage: wrote services config" "$JOB_LOG_FILE"; then
  phase_start "Restart Homepage after config sync"
  if "${KUBECTL[@]}" -n "$NAMESPACE" get deploy/homepage >/dev/null 2>&1; then
    info "Homepage services config changed; restarting deployment/homepage."
    "${KUBECTL[@]}" -n "$NAMESPACE" rollout restart deployment/homepage
    "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deployment/homepage --timeout=180s
    phase_end "ok"
  else
    info "deployment/homepage not found in namespace/$NAMESPACE; skipping restart."
    phase_end "skipped"
  fi
fi

if grep -q "Bazarr: wrote integration config" "$JOB_LOG_FILE"; then
  phase_start "Restart Bazarr after config sync"
  if "${KUBECTL[@]}" -n "$NAMESPACE" get deploy/bazarr >/dev/null 2>&1; then
    info "Bazarr integration config changed; restarting deployment/bazarr."
    "${KUBECTL[@]}" -n "$NAMESPACE" rollout restart deployment/bazarr
    "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deployment/bazarr --timeout=180s
    phase_end "ok"
  else
    info "deployment/bazarr not found in namespace/$NAMESPACE; skipping restart."
    phase_end "skipped"
  fi
fi

phase_start "Activate Jellyfin plugins (restart if needed)"
if "${KUBECTL[@]}" -n "$NAMESPACE" get deploy/jellyfin >/dev/null 2>&1; then
  JELLYFIN_API_KEY="$("${KUBECTL[@]}" -n "$NAMESPACE" get secret media-stack-secrets -o jsonpath='{.data.JELLYFIN_API_KEY}' 2>/dev/null | base64 -d || true)"
  if [[ -n "$JELLYFIN_API_KEY" ]]; then
    plugin_json="$("${KUBECTL[@]}" -n "$NAMESPACE" exec deploy/jellyfin -- sh -lc "curl -fsS 'http://localhost:8096/Plugins?api_key=${JELLYFIN_API_KEY}'" 2>/dev/null || true)"
    restart_count=0
    if command -v jq >/dev/null 2>&1; then
      restart_count="$(printf '%s' "$plugin_json" | jq '[.[] | select(.Status == "Restart")] | length' 2>/dev/null || echo 0)"
    else
      restart_count="$(printf '%s' "$plugin_json" | grep -c '"Status":"Restart"' || true)"
    fi
    restart_count="${restart_count:-0}"
    if [[ "$restart_count" =~ ^[0-9]+$ ]] && (( restart_count > 0 )); then
      info "Detected ${restart_count} Jellyfin plugin(s) pending restart; restarting deployment/jellyfin."
      "${KUBECTL[@]}" -n "$NAMESPACE" rollout restart deployment/jellyfin
      "${KUBECTL[@]}" -n "$NAMESPACE" rollout status deployment/jellyfin --timeout=300s
      info "Jellyfin restarted to activate pending plugin changes."
      phase_end "ok"
    else
      info "No Jellyfin plugin restart pending."
      phase_end "skipped"
    fi
  else
    info "JELLYFIN_API_KEY not found in secret; skipping Jellyfin plugin activation restart."
    phase_end "skipped"
  fi
else
  info "deployment/jellyfin not found in namespace/$NAMESPACE; skipping restart check."
  phase_end "skipped"
fi

info "Bootstrap job completed."
print_phase_summary
notify "ok" "media-stack bootstrap job completed (namespace=$NAMESPACE)"
