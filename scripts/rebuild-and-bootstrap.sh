#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAMESPACE="${NAMESPACE:-media-stack}"
SECRET_NAME="${SECRET_NAME:-media-stack-secrets}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-20m}"
DELETE_NAMESPACE="${DELETE_NAMESPACE:-1}"
INCLUDE_OPTIONAL="${INCLUDE_OPTIONAL:-}"
ENABLE_UNPACKERR="${ENABLE_UNPACKERR:-}"
RUN_BOOTSTRAP="${RUN_BOOTSTRAP:-}"
RUN_SMOKE_TEST="${RUN_SMOKE_TEST:-1}"
SKIP_PREPARE_HOST="${SKIP_PREPARE_HOST:-0}"
PREPARE_HOST_ROOT="${PREPARE_HOST_ROOT:-/srv/media-stack}"
STORAGE_MODE="${STORAGE_MODE:-dynamic-pvc}"
PVC_STORAGE_CLASS="${PVC_STORAGE_CLASS:-}"
INGRESS_DOMAIN="${INGRESS_DOMAIN:-local}"
CONFIG_FILE="${CONFIG_FILE:-$ROOT_DIR/bootstrap/media-stack.bootstrap.json}"
INGRESS_CLASS="${INGRESS_CLASS:-auto}"
PROFILE="${PROFILE:-full}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"
GENERATE_SECRETS_ON_REBUILD="${GENERATE_SECRETS_ON_REBUILD:-0}"
PRESERVE_SECRET_ON_REBUILD="${PRESERVE_SECRET_ON_REBUILD:-1}"
NODE_IP="${NODE_IP:-}"
RUN_START_EPOCH="$(date +%s)"
CURRENT_PHASE=""
CURRENT_PHASE_START=0
SECRET_BACKUP_FILE=""
SECRET_BACKUP_PRESENT=0
declare -a PHASE_NAMES=()
declare -a PHASE_RESULTS=()
declare -a PHASE_SECONDS=()

usage() {
  cat <<'EOF'
Usage:
  scripts/rebuild-and-bootstrap.sh [--namespace NS] [--ingress-domain DOMAIN] [--storage-class CLASS] [NODE_IP]

Description:
  Full automation helper for media-stack:
  1) optionally (re)prepare host folders
  2) optionally delete namespace
  3) apply profile manifests
  4) auto-patch ingress class if needed
  5) wait for deployments
  6) run bootstrap pipeline (profile-controlled)
  7) run ingress smoke test

Environment variables:
  PROFILE            (default: full; one of minimal/full/public-demo/power-user)
  NAMESPACE          (default: media-stack)
  CONFIG_FILE        (default: bootstrap/media-stack.bootstrap.json)
  WAIT_TIMEOUT       (default: 20m)
  DELETE_NAMESPACE   (default: 1)
  INCLUDE_OPTIONAL   (default by profile)
  ENABLE_UNPACKERR   (default by profile)
  RUN_BOOTSTRAP      (default by profile; public-demo defaults to 0)
  RUN_SMOKE_TEST     (default: 1)
  STORAGE_MODE       dynamic-pvc|legacy-hostpath (default: dynamic-pvc)
  PVC_STORAGE_CLASS  optional storageClassName to inject into all stack PVCs
  SKIP_PREPARE_HOST  (default: 0)
  PREPARE_HOST_ROOT  (default: /srv/media-stack)
  INGRESS_DOMAIN     (default: local)
  INGRESS_CLASS      (default: auto; picks public/nginx/first available)
  ALERT_WEBHOOK_URL  (optional; POSTs JSON status updates)
  GENERATE_SECRETS_ON_REBUILD (default: 0; set to 1 to run generate-secrets after apply)
  PRESERVE_SECRET_ON_REBUILD (default: 1; preserve media-stack-secrets across namespace rebuilds)
  SECRET_NAME        (default: media-stack-secrets)
  NODE_IP            (optional; can also be passed as arg)
EOF
}

ts() { date +"%Y-%m-%dT%H:%M:%S%z"; }
info() { echo "[$(ts)] [INFO] $*"; }
warn() { echo "[$(ts)] [WARN] $*" >&2; }
err() { echo "[$(ts)] [ERR] $*" >&2; exit 1; }

json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  printf '%s' "$s"
}

escape_sed_replacement() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//&/\\&}"
  printf '%s' "$value"
}

stream_with_manifest_overrides() {
  local ns_escaped root_escaped ingress_domain_escaped
  ns_escaped="$(escape_sed_replacement "$NAMESPACE")"
  root_escaped="$(escape_sed_replacement "$PREPARE_HOST_ROOT")"
  ingress_domain_escaped="$(escape_sed_replacement "$INGRESS_DOMAIN")"

  sed \
    -e "s#namespace:[[:space:]]*media-stack#namespace: ${ns_escaped}#g" \
    -e "s#name:[[:space:]]*media-stack\$#name: ${ns_escaped}#g" \
    -e "s#/srv/media-stack#${root_escaped}#g" \
    -E -e "s#([A-Za-z0-9-]+)\\.local#\\1.${ingress_domain_escaped}#g"
}

inject_storage_class() {
  if [[ -z "$PVC_STORAGE_CLASS" ]]; then
    cat
    return 0
  fi
  awk -v cls="$PVC_STORAGE_CLASS" '
    BEGIN {in_pvc=0; in_spec=0; inserted=0}
    /^kind:[[:space:]]*PersistentVolumeClaim[[:space:]]*$/ {
      in_pvc=1; in_spec=0; inserted=0; print; next
    }
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
  '
}

apply_manifest_file_with_overrides() {
  local file="$1"
  stream_with_manifest_overrides <"$file" | inject_storage_class | "${KUBECTL[@]}" apply -f -
}

cleanup_secret_backup() {
  if [[ -n "${SECRET_BACKUP_FILE:-}" && -f "$SECRET_BACKUP_FILE" ]]; then
    rm -f "$SECRET_BACKUP_FILE" || true
  fi
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

backup_existing_secret_values() {
  local key encoded
  local -a keys=(
    QBITTORRENT_USERNAME
    QBITTORRENT_PASSWORD
    SABNZBD_API_KEY
    STACK_ADMIN_USERNAME
    STACK_ADMIN_PASSWORD
    JELLYFIN_API_KEY
    JELLYFIN_USER_ID
    UNPACKERR_SONARR_API_KEY
    UNPACKERR_RADARR_API_KEY
    UNPACKERR_LIDARR_API_KEY
    UNPACKERR_READARR_API_KEY
  )

  if [[ "$PRESERVE_SECRET_ON_REBUILD" != "1" ]]; then
    info "Secret preservation disabled (PRESERVE_SECRET_ON_REBUILD=0)."
    return 0
  fi

  if ! "${KUBECTL[@]}" -n "$NAMESPACE" get secret "$SECRET_NAME" >/dev/null 2>&1; then
    info "No existing secret ${NAMESPACE}/${SECRET_NAME} found to preserve."
    return 0
  fi

  SECRET_BACKUP_FILE="$(mktemp -t media-stack-secret-backup.XXXXXX)"
  chmod 600 "$SECRET_BACKUP_FILE"
  : >"$SECRET_BACKUP_FILE"

  for key in "${keys[@]}"; do
    encoded="$("${KUBECTL[@]}" -n "$NAMESPACE" get secret "$SECRET_NAME" -o "jsonpath={.data.${key}}" 2>/dev/null || true)"
    if [[ -n "$encoded" ]]; then
      printf '%s=%s\n' "$key" "$encoded" >>"$SECRET_BACKUP_FILE"
    fi
  done

  if [[ -s "$SECRET_BACKUP_FILE" ]]; then
    SECRET_BACKUP_PRESENT=1
    info "Backed up $(wc -l < "$SECRET_BACKUP_FILE" | awk '{print $1}') secret key(s) from ${NAMESPACE}/${SECRET_NAME}."
  else
    SECRET_BACKUP_PRESENT=0
    info "Secret ${NAMESPACE}/${SECRET_NAME} exists but has no matching keys to preserve."
  fi
}

restore_secret_values_from_backup() {
  local key encoded decoded
  local patch='{"stringData":{'
  local first=1

  if [[ "$SECRET_BACKUP_PRESENT" != "1" || -z "${SECRET_BACKUP_FILE:-}" || ! -s "$SECRET_BACKUP_FILE" ]]; then
    info "No preserved secret values to restore."
    return 0
  fi

  if ! "${KUBECTL[@]}" -n "$NAMESPACE" get secret "$SECRET_NAME" >/dev/null 2>&1; then
    info "Secret ${NAMESPACE}/${SECRET_NAME} missing after apply; creating it before restore."
    cat <<EOF | "${KUBECTL[@]}" apply -f -
apiVersion: v1
kind: Secret
metadata:
  name: ${SECRET_NAME}
  namespace: ${NAMESPACE}
type: Opaque
stringData: {}
EOF
  fi

  while IFS='=' read -r key encoded; do
    [[ -z "$key" || -z "$encoded" ]] && continue
    decoded="$(printf '%s' "$encoded" | base64 -d 2>/dev/null || true)"
    [[ -z "$decoded" ]] && continue
    if (( first == 0 )); then
      patch+=","
    fi
    patch+="\"$(json_escape "$key")\":\"$(json_escape "$decoded")\""
    first=0
  done <"$SECRET_BACKUP_FILE"
  patch+='}}'

  if (( first == 1 )); then
    info "Preserved secret backup had no restorable values."
    return 0
  fi

  "${KUBECTL[@]}" -n "$NAMESPACE" patch secret "$SECRET_NAME" --type merge -p "$patch" >/dev/null
  info "Restored preserved values into ${NAMESPACE}/${SECRET_NAME}."
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
  warn "Rebuild/bootstrap failed at line ${line} while running: ${cmd}"
  if declare -p KUBECTL >/dev/null 2>&1; then
    warn "Pod status snapshot at failure:"
    "${KUBECTL[@]}" -n "$NAMESPACE" get pods -o wide || true
  fi
  print_phase_summary
  notify "error" "media-stack rebuild/bootstrap failed (profile=$PROFILE, namespace=$NAMESPACE)"
  exit "$code"
}

trap 'on_error $LINENO "$BASH_COMMAND"' ERR
trap cleanup_secret_backup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --namespace)
      NAMESPACE="${2:-}"
      shift 2
      ;;
    --ingress-domain)
      INGRESS_DOMAIN="${2:-}"
      shift 2
      ;;
    --storage-class)
      PVC_STORAGE_CLASS="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "${POSITIONAL_NODE_IP_SET:-}" ]]; then
        NODE_IP="$1"
        POSITIONAL_NODE_IP_SET=1
        shift
      else
        err "Unknown argument: $1"
      fi
      ;;
  esac
done

if [[ "${NODE_IP:-}" == "-h" || "${NODE_IP:-}" == "--help" ]]; then
  usage
  exit 0
fi

if command -v microk8s >/dev/null 2>&1; then
  KUBECTL=(microk8s kubectl)
elif command -v kubectl >/dev/null 2>&1; then
  KUBECTL=(kubectl)
else
  err "Neither microk8s nor kubectl is available in PATH."
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  err "Config file not found: $CONFIG_FILE"
fi
if [[ -z "$NAMESPACE" ]]; then
  err "NAMESPACE cannot be empty."
fi
INGRESS_DOMAIN="${INGRESS_DOMAIN#.}"
if [[ -z "$INGRESS_DOMAIN" ]]; then
  err "INGRESS_DOMAIN cannot be empty."
fi
case "$STORAGE_MODE" in
  dynamic-pvc|legacy-hostpath) ;;
  *) err "Unsupported STORAGE_MODE '$STORAGE_MODE'. Use dynamic-pvc|legacy-hostpath." ;;
esac

apply_profile_defaults() {
  case "$PROFILE" in
    minimal)
      : "${INCLUDE_OPTIONAL:=0}"
      : "${ENABLE_UNPACKERR:=0}"
      : "${RUN_BOOTSTRAP:=1}"
      ;;
    full)
      : "${INCLUDE_OPTIONAL:=1}"
      : "${ENABLE_UNPACKERR:=1}"
      : "${RUN_BOOTSTRAP:=1}"
      ;;
    public-demo)
      : "${INCLUDE_OPTIONAL:=1}"
      : "${ENABLE_UNPACKERR:=0}"
      : "${RUN_BOOTSTRAP:=0}"
      ;;
    power-user)
      : "${INCLUDE_OPTIONAL:=1}"
      : "${ENABLE_UNPACKERR:=1}"
      : "${RUN_BOOTSTRAP:=1}"
      ;;
    *)
      err "Unknown PROFILE '$PROFILE'. Supported: minimal, full, public-demo, power-user."
      ;;
  esac
}

apply_manifests_for_profile() {
  local profile_dir="$ROOT_DIR/k8s/profiles/$PROFILE"
  local build_failed=0
  local kustomize_output=""
  if [[ -d "$profile_dir" ]]; then
    info "Applying manifests for profile '$PROFILE' via $profile_dir (namespace/path overrides enabled)"
    if kustomize_output="$("${KUBECTL[@]}" kustomize --load-restrictor=LoadRestrictionsNone "$profile_dir" 2>&1)"; then
      printf '%s\n' "$kustomize_output" | stream_with_manifest_overrides | inject_storage_class | "${KUBECTL[@]}" apply -f -
      return 0
    fi
    warn "Profile kustomize build failed: $(printf '%s\n' "$kustomize_output" | tail -n1)"
    build_failed=1
  fi

  if (( build_failed == 1 )); then
    warn "Profile kustomize build failed (possibly load restrictions or invalid profile resources)."
    warn "Falling back to direct manifest apply for profile '$PROFILE'."
  else
    warn "Profile directory not found for '$PROFILE'; falling back to direct manifest apply."
  fi

  if kustomize_output="$("${KUBECTL[@]}" kustomize --load-restrictor=LoadRestrictionsNone "$ROOT_DIR/k8s" 2>&1)"; then
    printf '%s\n' "$kustomize_output" | stream_with_manifest_overrides | inject_storage_class | "${KUBECTL[@]}" apply -f -
  else
    warn "Base kustomize build failed: $(printf '%s\n' "$kustomize_output" | tail -n1)"
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/namespace.yaml"
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/hardening.yaml"
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/secrets.example.yaml"
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/storage-pvc.yaml"
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/core.yaml"
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/ingress-traefik.yaml"
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/scale-policy.yaml"
  fi

  if [[ "$PROFILE" == "full" || "$PROFILE" == "public-demo" || "$PROFILE" == "power-user" || "$INCLUDE_OPTIONAL" == "1" ]]; then
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/optional.yaml"
  fi

  if [[ "$PROFILE" == "full" || "$PROFILE" == "power-user" || "$ENABLE_UNPACKERR" == "1" ]]; then
    apply_manifest_file_with_overrides "$ROOT_DIR/k8s/unpackerr.yaml"
  fi

  if [[ "$PROFILE" == "public-demo" ]]; then
    local app
    for app in qbittorrent sonarr radarr lidarr readarr bazarr sabnzbd; do
      if "${KUBECTL[@]}" -n "$NAMESPACE" get deploy "$app" >/dev/null 2>&1; then
        info "public-demo profile: scaling deploy/$app to 0"
        "${KUBECTL[@]}" -n "$NAMESPACE" scale "deploy/$app" --replicas=0
      else
        info "public-demo profile: deploy/$app not installed; skipping scale-to-zero patch"
      fi
    done
  fi

  if [[ "$PROFILE" == "power-user" ]]; then
    local tls_patch
    tls_patch="$(cat <<'JSON'
{
  "spec": {
    "tls": [
      {
        "secretName": "media-stack-tls",
        "hosts": [
          "homepage.local",
          "jellyfin.local",
          "jellyseerr.local",
          "sonarr.local",
          "radarr.local",
          "lidarr.local",
          "readarr.local",
          "bazarr.local",
          "prowlarr.local",
          "qbittorrent.local",
          "sabnzbd.local",
          "tautulli.local"
        ]
      }
    ]
  }
}
JSON
)"
    info "power-user profile: applying TLS hosts patch to ingress/media-stack-ingress"
    "${KUBECTL[@]}" -n "$NAMESPACE" patch ingress media-stack-ingress --type merge -p "$tls_patch"
  fi
}

wait_for_namespace_deleted() {
  local max_wait=600
  local waited=0
  while "${KUBECTL[@]}" get namespace "$NAMESPACE" >/dev/null 2>&1; do
    if (( waited >= max_wait )); then
      err "Namespace '$NAMESPACE' is still terminating after ${max_wait}s."
    fi
    info "Waiting for namespace/$NAMESPACE deletion (elapsed ${waited}s)"
    sleep 5
    waited=$((waited + 5))
  done
}

pick_ingress_class() {
  if [[ "$INGRESS_CLASS" != "auto" ]]; then
    echo "$INGRESS_CLASS"
    return 0
  fi

  mapfile -t classes < <(
    "${KUBECTL[@]}" get ingressclass -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' \
      2>/dev/null || true
  )

  local cls
  for cls in "${classes[@]}"; do
    if [[ "$cls" == "public" ]]; then
      echo "public"
      return 0
    fi
  done
  for cls in "${classes[@]}"; do
    if [[ "$cls" == "nginx" ]]; then
      echo "nginx"
      return 0
    fi
  done
  if [[ "${#classes[@]}" -gt 0 && -n "${classes[0]}" ]]; then
    echo "${classes[0]}"
    return 0
  fi

  echo ""
}

wait_for_deployments() {
  local failed=0
  mapfile -t deploys < <(
    "${KUBECTL[@]}" -n "$NAMESPACE" get deploy -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
  )

  if [[ "${#deploys[@]}" -eq 0 ]]; then
    err "No deployments found in namespace '$NAMESPACE'."
  fi

  local deploy replicas
  for deploy in "${deploys[@]}"; do
    [[ -z "$deploy" ]] && continue
    replicas="$("${KUBECTL[@]}" -n "$NAMESPACE" get deploy "$deploy" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo 1)"
    if [[ "${replicas:-1}" == "0" ]]; then
      info "Skipping rollout wait for deploy/$deploy (replicas=0)"
      continue
    fi
    info "Waiting for deploy/$deploy rollout"
    if ! "${KUBECTL[@]}" -n "$NAMESPACE" rollout status "deploy/$deploy" --timeout="$WAIT_TIMEOUT"; then
      warn "deploy/$deploy not ready within $WAIT_TIMEOUT"
      failed=$((failed + 1))
    fi
  done

  if (( failed > 0 )); then
    "${KUBECTL[@]}" -n "$NAMESPACE" get pods -o wide || true
    err "$failed deployment(s) failed readiness checks."
  fi
}

info "Starting full media-stack rebuild/bootstrap"
phase_start "Resolve profile defaults"
apply_profile_defaults
phase_end "ok"
info "Namespace: $NAMESPACE"
info "Profile: $PROFILE"
info "Ingress domain: $INGRESS_DOMAIN"
info "Config: $CONFIG_FILE"
info "Delete namespace: $DELETE_NAMESPACE"
info "Storage mode: $STORAGE_MODE"
if [[ -n "$PVC_STORAGE_CLASS" ]]; then
  info "PVC storage class override: $PVC_STORAGE_CLASS"
else
  info "PVC storage class override: <cluster default>"
fi
info "Include optional: $INCLUDE_OPTIONAL"
info "Enable Unpackerr: $ENABLE_UNPACKERR"
info "Run bootstrap: $RUN_BOOTSTRAP"
info "Generate secrets on rebuild: $GENERATE_SECRETS_ON_REBUILD"
info "Preserve secret on rebuild: $PRESERVE_SECRET_ON_REBUILD"
notify "info" "media-stack rebuild/bootstrap started (profile=$PROFILE, namespace=$NAMESPACE)"

phase_start "Validate bootstrap config schema"
python3 "$ROOT_DIR/scripts/validate-bootstrap-config.py" --config "$CONFIG_FILE"
phase_end "ok"

if [[ "$SKIP_PREPARE_HOST" != "1" ]]; then
  phase_start "Prepare host directories"
  if [[ "$STORAGE_MODE" == "legacy-hostpath" ]]; then
    info "Preparing host directories under $PREPARE_HOST_ROOT"
    bash "$ROOT_DIR/scripts/prepare-host.sh" "$PREPARE_HOST_ROOT"
    phase_end "ok"
  else
    info "Skipping host directory prep (storage mode: dynamic-pvc)"
    phase_end "skipped"
  fi
else
  phase_start "Prepare host directories"
  phase_end "skipped"
fi

phase_start "Backup existing credentials"
backup_existing_secret_values
phase_end "ok"

phase_start "Delete namespace (optional)"
if [[ "$DELETE_NAMESPACE" == "1" ]]; then
  if "${KUBECTL[@]}" get namespace "$NAMESPACE" >/dev/null 2>&1; then
    info "Deleting namespace/$NAMESPACE"
    "${KUBECTL[@]}" delete namespace "$NAMESPACE" --wait=false
    wait_for_namespace_deleted
  else
    info "Namespace/$NAMESPACE does not exist; continuing"
  fi
  phase_end "ok"
else
  phase_end "skipped"
fi

phase_start "Apply manifests for profile"
apply_manifests_for_profile
phase_end "ok"

if [[ "$GENERATE_SECRETS_ON_REBUILD" == "1" ]]; then
  phase_start "Generate secrets"
  info "Generating secure secrets in cluster before bootstrap"
  NAMESPACE="$NAMESPACE" OUTPUT_FILE="$ROOT_DIR/secrets.generated.env" \
    bash "$ROOT_DIR/scripts/generate-secrets.sh"
  phase_end "ok"
else
  phase_start "Generate secrets"
  phase_end "skipped"
fi

phase_start "Restore preserved credentials"
restore_secret_values_from_backup
phase_end "ok"

phase_start "Patch ingress class"
desired_class="$(pick_ingress_class)"
if [[ -n "$desired_class" ]]; then
  current_class="$("${KUBECTL[@]}" -n "$NAMESPACE" get ingress media-stack-ingress -o jsonpath='{.spec.ingressClassName}' 2>/dev/null || true)"
  if [[ "$current_class" != "$desired_class" ]]; then
    info "Patching ingress class to '$desired_class' (current: '${current_class:-<empty>}')"
    NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/microk8s-patch-ingress-class.sh" "$desired_class"
  else
    info "Ingress class already set to '$desired_class'"
  fi
  phase_end "ok"
else
  warn "No ingress classes discovered; skipping ingress patch."
  phase_end "skipped"
fi

phase_start "Wait for deployments"
wait_for_deployments
phase_end "ok"

if [[ "$RUN_BOOTSTRAP" == "1" ]]; then
  phase_start "Apply scale-policy guardrails"
  info "Applying scale-policy guardrails"
  NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/apply-scale-policy.sh"
  phase_end "ok"
else
  phase_start "Apply scale-policy guardrails"
  info "Scale-policy guardrails skipped for non-bootstrap profile."
  phase_end "skipped"
fi

if [[ "$RUN_BOOTSTRAP" == "1" ]]; then
  phase_start "Run bootstrap pipeline"
  info "Running full bootstrap pipeline"
  NAMESPACE="$NAMESPACE" PREPARE_HOST_ROOT="$PREPARE_HOST_ROOT" ENABLE_UNPACKERR="$ENABLE_UNPACKERR" \
    bash "$ROOT_DIR/scripts/bootstrap-all.sh" "$CONFIG_FILE"
  phase_end "ok"
else
  phase_start "Run bootstrap pipeline"
  info "Bootstrap skipped by profile/policy."
  phase_end "skipped"
fi

if [[ "$RUN_SMOKE_TEST" == "1" ]]; then
  phase_start "Run ingress smoke test"
  if [[ -z "$NODE_IP" ]]; then
    NODE_IP="$(hostname -I | awk '{print $1}')"
  fi
  if [[ -n "$NODE_IP" ]]; then
    info "Running ingress smoke test against node IP $NODE_IP"
    NAMESPACE="$NAMESPACE" bash "$ROOT_DIR/scripts/microk8s-smoke-test.sh" "$NODE_IP"
    phase_end "ok"
  else
    warn "Could not detect NODE_IP; skipping smoke test."
    phase_end "skipped"
  fi
else
  phase_start "Run ingress smoke test"
  phase_end "skipped"
fi

phase_start "Collect final pod status"
info "Final pod status:"
"${KUBECTL[@]}" -n "$NAMESPACE" get pods
phase_end "ok"
print_phase_summary

echo
echo "[OK] Rebuild + bootstrap completed."
notify "ok" "media-stack rebuild/bootstrap succeeded (profile=$PROFILE, namespace=$NAMESPACE)"
