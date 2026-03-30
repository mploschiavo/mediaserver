#!/usr/bin/env bash

# Shared source-of-truth for files bundled into media-stack-bootstrap-script ConfigMap.
# Format: "<configmap-key>|<path-relative-to-repo-root>"
BOOTSTRAP_SCRIPT_CONFIGMAP_FILES=(
  "bootstrap_apps.py|scripts/bootstrap-apps.py"
  "bootstrap_lib__init__.py|scripts/bootstrap_lib/__init__.py"
  "bootstrap_lib__common.py|scripts/bootstrap_lib/common.py"
  "bootstrap_lib__http_client.py|scripts/bootstrap_lib/http_client.py"
  "bootstrap_lib__servarr.py|scripts/bootstrap_lib/servarr.py"
  "bootstrap_lib__homepage.py|scripts/bootstrap_lib/homepage.py"
  "bootstrap_lib__bazarr.py|scripts/bootstrap_lib/bazarr.py"
  "bootstrap_lib__jellyfin.py|scripts/bootstrap_lib/jellyfin.py"
  "bootstrap_lib__defaults.py|scripts/bootstrap_lib/defaults.py"
  "bootstrap_defaults__maintainerr_policy.json|scripts/bootstrap_defaults/maintainerr_policy.json"
  "bootstrap_defaults__jellyfin_home_rails.json|scripts/bootstrap_defaults/jellyfin_home_rails.json"
  "bootstrap_services__init__.py|scripts/bootstrap_services/__init__.py"
  "bootstrap_services__arr_service.py|scripts/bootstrap_services/arr_service.py"
  "bootstrap_services__arr_queue_cleanup_service.py|scripts/bootstrap_services/arr_queue_cleanup_service.py"
  "bootstrap_services__auth_service.py|scripts/bootstrap_services/auth_service.py"
  "bootstrap_services__config_models.py|scripts/bootstrap_services/config_models.py"
  "bootstrap_services__discovery_lists_service.py|scripts/bootstrap_services/discovery_lists_service.py"
  "bootstrap_services__health_service.py|scripts/bootstrap_services/health_service.py"
  "bootstrap_services__bazarr_service.py|scripts/bootstrap_services/bazarr_service.py"
  "bootstrap_services__jellyfin_service.py|scripts/bootstrap_services/jellyfin_service.py"
  "bootstrap_services__jellyfin_home_rails_service.py|scripts/bootstrap_services/jellyfin_home_rails_service.py"
  "bootstrap_services__jellyseerr_service.py|scripts/bootstrap_services/jellyseerr_service.py"
  "bootstrap_services__media_hygiene_ops_service.py|scripts/bootstrap_services/media_hygiene_ops_service.py"
  "bootstrap_services__media_hygiene_service.py|scripts/bootstrap_services/media_hygiene_service.py"
  "bootstrap_services__prowlarr_service.py|scripts/bootstrap_services/prowlarr_service.py"
  "bootstrap_services__qbit_service.py|scripts/bootstrap_services/qbit_service.py"
  "bootstrap_services__sabnzbd_service.py|scripts/bootstrap_services/sabnzbd_service.py"
  "core__init__.py|scripts/core/__init__.py"
  "core__decorators.py|scripts/core/decorators.py"
  "core__http.py|scripts/core/http.py"
)

bootstrap_script_configmap_create_yaml() {
  local namespace="$1"
  local root_dir="$2"
  local output_file="$3"
  local entry key rel
  local -a args=()

  for entry in "${BOOTSTRAP_SCRIPT_CONFIGMAP_FILES[@]}"; do
    IFS='|' read -r key rel <<<"$entry"
    args+=(--from-file="${key}=${root_dir}/${rel}")
  done

  "${KUBECTL[@]}" -n "$namespace" create configmap media-stack-bootstrap-script \
    "${args[@]}" \
    --dry-run=client -o yaml >"$output_file"
}
