#!/usr/bin/env bash
set -Eeuo pipefail

NAMESPACE="${NAMESPACE:-media-stack}"
TIMEOUT="${TIMEOUT:-20m}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HEARTBEAT_INTERVAL="${HEARTBEAT_INTERVAL:-15}"
PREPARE_HOST_ROOT="${PREPARE_HOST_ROOT:-/srv/media-stack}"
HB_PID=""
RUN_START_EPOCH="$(date +%s)"
CURRENT_PHASE=""
CURRENT_PHASE_START=0
declare -a PHASE_NAMES=()
declare -a PHASE_RESULTS=()
declare -a PHASE_SECONDS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/run-prowlarr-auto-indexers.sh

Description:
  Auto-discovers Prowlarr indexer templates/presets, tests each, and adds only
  those that pass. No indexer JSON editing required.

Environment variables:
  NAMESPACE   (default: media-stack)
  TIMEOUT     (default: 20m)
  HEARTBEAT_INTERVAL (default: 15)
  PREPARE_HOST_ROOT (default: /srv/media-stack)
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
    -e "s#/srv/media-stack#${root_escaped}#g"
}

ensure_auto_indexer_pvc_prereqs() {
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
    info "Ensuring auto-indexer PVC prerequisites via $storage_manifest"
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
    warn "Missing required PVC(s) for auto-indexer job: ${missing[*]}"
    warn "Apply storage PVCs and retry: ${KUBECTL[*]} apply -f $ROOT_DIR/k8s/storage-pvc.yaml"
    return 1
  fi

  info "Auto-indexer PVC prerequisites are present."
  return 0
}

cleanup() {
  if [[ -n "$HB_PID" ]] && kill -0 "$HB_PID" >/dev/null 2>&1; then
    kill "$HB_PID" >/dev/null 2>&1 || true
  fi
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
  warn "Auto-indexer job runner failed at line ${line} while running: ${cmd}"
  cleanup
  print_phase_summary
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
            warn "Auto-indexer pod is Pending: ${pod_name} (reason=${sched_reason:-unknown})"
            if [[ -n "$sched_message" ]]; then
              warn "Auto-indexer scheduling message: $sched_message"
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
  local raw="${1:-20m}"
  local num unit
  if [[ "$raw" =~ ^([0-9]+)([smh]?)$ ]]; then
    num="${BASH_REMATCH[1]}"
    unit="${BASH_REMATCH[2]}"
    case "$unit" in
      h) echo $((num * 3600)) ;;
      m|"") echo $((num * 60)) ;;
      s) echo "$num" ;;
      *) echo 1200 ;;
    esac
    return 0
  fi
  echo 1200
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
    if [[ "$failed_condition" == "True" || "$backoff_condition" == "True" ]]; then
      return 1
    fi
    if [[ "$failed" =~ ^[0-9]+$ ]] && (( failed >= 1 )); then
      return 1
    fi

    pod_name="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l job-name="$job_name" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
    pod_phase="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l job-name="$job_name" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)"
    if [[ "$pod_phase" == "Failed" || "$pod_phase" == "Unknown" ]]; then
      return 1
    fi
    if [[ "$pod_phase" == "Pending" && -n "$pod_name" ]]; then
      pod_sched_message="$("${KUBECTL[@]}" -n "$NAMESPACE" get pod "$pod_name" -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].message}' 2>/dev/null || true)"
      elapsed=$(( $(date +%s) - start ))
      if (( elapsed >= 20 )) && [[ "$pod_sched_message" =~ persistentvolumeclaim.*not\ found ]]; then
        warn "Auto-indexer pod remained Pending because required PVCs are missing."
        warn "Scheduling message: ${pod_sched_message}"
        return 1
      fi
      if (( elapsed >= 120 )) && [[ "$pod_sched_message" =~ persistentvolumeclaim|unbound\ immediate\ PersistentVolumeClaims|volume\ node\ affinity\ conflict|Multi-Attach|didn\'t\ match\ Pod\'s\ node\ affinity ]]; then
        warn "Auto-indexer pod remained Pending with a hard scheduling/storage error for ${elapsed}s."
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

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
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
info "Heartbeat interval: ${HEARTBEAT_INTERVAL}s"
info "Host root: $PREPARE_HOST_ROOT"

phase_start "Ensure auto-indexer PVC prerequisites"
ensure_auto_indexer_pvc_prereqs
phase_end "ok"

phase_start "Update bootstrap script ConfigMap"
info "Updating bootstrap script ConfigMap for auto-indexer job"
bootstrap_script_cm_yaml="$(mktemp -t media-stack-bootstrap-script.auto.XXXXXX.yaml)"
bootstrap_auto_cfg_cm_yaml="$(mktemp -t media-stack-bootstrap-config-auto.XXXXXX.yaml)"
"${KUBECTL[@]}" -n "$NAMESPACE" create configmap media-stack-bootstrap-script \
  --from-file=bootstrap_apps.py="$ROOT_DIR/scripts/bootstrap-apps.py" \
  --from-file=__init__.py="$ROOT_DIR/scripts/bootstrap_lib/__init__.py" \
  --from-file=common.py="$ROOT_DIR/scripts/bootstrap_lib/common.py" \
  --from-file=http_client.py="$ROOT_DIR/scripts/bootstrap_lib/http_client.py" \
  --from-file=servarr.py="$ROOT_DIR/scripts/bootstrap_lib/servarr.py" \
  --from-file=homepage.py="$ROOT_DIR/scripts/bootstrap_lib/homepage.py" \
  --from-file=bazarr.py="$ROOT_DIR/scripts/bootstrap_lib/bazarr.py" \
  --from-file=jellyfin.py="$ROOT_DIR/scripts/bootstrap_lib/jellyfin.py" \
  --dry-run=client -o yaml >"$bootstrap_script_cm_yaml"
if ! "${KUBECTL[@]}" -n "$NAMESPACE" replace -f "$bootstrap_script_cm_yaml" >/dev/null 2>&1; then
  "${KUBECTL[@]}" -n "$NAMESPACE" create -f "$bootstrap_script_cm_yaml" >/dev/null
else
  info "configmap/media-stack-bootstrap-script replaced"
fi
phase_end "ok"

phase_start "Update auto-indexer config ConfigMap"
info "Updating temporary bootstrap config ConfigMap"
cat <<EOF | "${KUBECTL[@]}" -n "$NAMESPACE" create configmap media-stack-bootstrap-config-auto \
  --from-file=config.json=/dev/stdin \
  --dry-run=client -o yaml >"$bootstrap_auto_cfg_cm_yaml"
{
  "prowlarr_url": "http://prowlarr:9696",
  "trigger_indexer_sync": true,
  "arr_apps": []
}
EOF
if ! "${KUBECTL[@]}" -n "$NAMESPACE" replace -f "$bootstrap_auto_cfg_cm_yaml" >/dev/null 2>&1; then
  "${KUBECTL[@]}" -n "$NAMESPACE" create -f "$bootstrap_auto_cfg_cm_yaml" >/dev/null
else
  info "configmap/media-stack-bootstrap-config-auto replaced"
fi
rm -f "$bootstrap_script_cm_yaml" "$bootstrap_auto_cfg_cm_yaml"
phase_end "ok"

phase_start "Recreate auto-indexer Job"
"${KUBECTL[@]}" -n "$NAMESPACE" delete job media-stack-prowlarr-auto-indexers --ignore-not-found

info "Creating auto-indexer Job"
cat <<EOF | "${KUBECTL[@]}" apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: media-stack-prowlarr-auto-indexers
  namespace: $NAMESPACE
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 600
  template:
    metadata:
      labels:
        app: media-stack-prowlarr-auto-indexers
    spec:
      restartPolicy: Never
      containers:
        - name: bootstrap
          image: python:3.12-alpine
          imagePullPolicy: IfNotPresent
          env:
            - name: PROWLARR_API_KEY
              valueFrom:
                secretKeyRef:
                  name: media-stack-secrets
                  key: PROWLARR_API_KEY
                  optional: true
            - name: BOOTSTRAP_WAIT_INTERVAL_SECONDS
              value: "3"
            - name: BOOTSTRAP_WAIT_HEARTBEAT_SECONDS
              value: "15"
            - name: AUTO_INDEXER_HEARTBEAT_EVERY
              value: "25"
            - name: BOOTSTRAP_ALT_CONFIG_ROOT
              value: /srv-host-config
          command:
            - python
            - /bootstrap/bootstrap_apps.py
            - --config
            - /bootstrap-config/config.json
            - --config-root
            - /srv-config
            - --auto-prowlarr-indexers
          volumeMounts:
            - name: bootstrap-script
              mountPath: /bootstrap
              readOnly: true
            - name: bootstrap-config
              mountPath: /bootstrap-config
              readOnly: true
            - name: cfg-jellyfin
              mountPath: /srv-config/jellyfin
            - name: cfg-jellyseerr
              mountPath: /srv-config/jellyseerr
            - name: cfg-sonarr
              mountPath: /srv-config/sonarr
            - name: cfg-radarr
              mountPath: /srv-config/radarr
            - name: cfg-lidarr
              mountPath: /srv-config/lidarr
            - name: cfg-readarr
              mountPath: /srv-config/readarr
            - name: cfg-bazarr
              mountPath: /srv-config/bazarr
            - name: cfg-prowlarr
              mountPath: /srv-config/prowlarr
            - name: cfg-sabnzbd
              mountPath: /srv-config/sabnzbd
            - name: cfg-homepage
              mountPath: /srv-config/homepage
            - name: cfg-maintainerr
              mountPath: /srv-config/maintainerr
            - name: cfg-jellyfin-auto-collections
              mountPath: /srv-config/jellyfin-auto-collections
            - name: host-config-fallback
              mountPath: /srv-host-config
              readOnly: true
            - name: stack-torrents
              mountPath: /srv-stack/data/torrents
            - name: stack-usenet
              mountPath: /srv-stack/data/usenet
            - name: stack-media
              mountPath: /srv-stack/media
      volumes:
        - name: bootstrap-script
          configMap:
            name: media-stack-bootstrap-script
            defaultMode: 0555
            items:
              - key: bootstrap_apps.py
                path: bootstrap_apps.py
              - key: __init__.py
                path: bootstrap_lib/__init__.py
              - key: common.py
                path: bootstrap_lib/common.py
              - key: http_client.py
                path: bootstrap_lib/http_client.py
              - key: servarr.py
                path: bootstrap_lib/servarr.py
              - key: homepage.py
                path: bootstrap_lib/homepage.py
              - key: bazarr.py
                path: bootstrap_lib/bazarr.py
              - key: jellyfin.py
                path: bootstrap_lib/jellyfin.py
        - name: bootstrap-config
          configMap:
            name: media-stack-bootstrap-config-auto
        - name: cfg-jellyfin
          persistentVolumeClaim:
            claimName: media-stack-config-jellyfin
        - name: cfg-jellyseerr
          persistentVolumeClaim:
            claimName: media-stack-config-jellyseerr
        - name: cfg-sonarr
          persistentVolumeClaim:
            claimName: media-stack-config-sonarr
        - name: cfg-radarr
          persistentVolumeClaim:
            claimName: media-stack-config-radarr
        - name: cfg-lidarr
          persistentVolumeClaim:
            claimName: media-stack-config-lidarr
        - name: cfg-readarr
          persistentVolumeClaim:
            claimName: media-stack-config-readarr
        - name: cfg-bazarr
          persistentVolumeClaim:
            claimName: media-stack-config-bazarr
        - name: cfg-prowlarr
          persistentVolumeClaim:
            claimName: media-stack-config-prowlarr
        - name: cfg-sabnzbd
          persistentVolumeClaim:
            claimName: media-stack-config-sabnzbd
        - name: cfg-homepage
          persistentVolumeClaim:
            claimName: media-stack-config-homepage
        - name: cfg-maintainerr
          persistentVolumeClaim:
            claimName: media-stack-config-maintainerr
        - name: cfg-jellyfin-auto-collections
          persistentVolumeClaim:
            claimName: media-stack-config-jellyfin-auto-collections
        - name: host-config-fallback
          hostPath:
            path: /srv/media-stack/config
            type: DirectoryOrCreate
        - name: stack-torrents
          persistentVolumeClaim:
            claimName: media-stack-data-torrents
        - name: stack-usenet
          persistentVolumeClaim:
            claimName: media-stack-data-usenet
        - name: stack-media
          persistentVolumeClaim:
            claimName: media-stack-media
EOF
phase_end "ok"

phase_start "Wait for auto-indexer Job completion"
start_job_heartbeat "media-stack-prowlarr-auto-indexers" "app=media-stack-prowlarr-auto-indexers"
TIMEOUT_SECONDS="$(parse_timeout_seconds "$TIMEOUT")"
wait_for_job_complete_or_fail "media-stack-prowlarr-auto-indexers" "$TIMEOUT_SECONDS" || job_wait_rc="$?"
job_wait_rc="${job_wait_rc:-0}"
if [[ "$job_wait_rc" != "0" ]]; then
  if [[ "$job_wait_rc" == "1" ]]; then
    warn "Job failed before completion."
  else
    warn "Job did not complete successfully within $TIMEOUT"
  fi
  "${KUBECTL[@]}" -n "$NAMESPACE" describe job media-stack-prowlarr-auto-indexers || true
  "${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=media-stack-prowlarr-auto-indexers -o wide || true
  pod_name="$("${KUBECTL[@]}" -n "$NAMESPACE" get pods -l app=media-stack-prowlarr-auto-indexers -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [[ -n "$pod_name" ]]; then
    "${KUBECTL[@]}" -n "$NAMESPACE" describe pod "$pod_name" || true
  fi
  "${KUBECTL[@]}" -n "$NAMESPACE" logs job/media-stack-prowlarr-auto-indexers --tail=300 --timestamps || true
  false
fi
phase_end "ok"

cleanup
phase_start "Print auto-indexer Job logs"
"${KUBECTL[@]}" -n "$NAMESPACE" logs job/media-stack-prowlarr-auto-indexers --tail=300 --timestamps
phase_end "ok"
info "Auto indexer bootstrap complete."
print_phase_summary
