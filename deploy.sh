#!/usr/bin/env bash
# Media Automation Stack deploy — reads profile YAML and runs docker compose.
#
# Usage:
#   ./deploy.sh                                              # standard profile
#   ./deploy.sh examples/bootstrap-profiles/my-profile.yaml  # custom profile
#
# The profile YAML is the single source of truth for storage paths, routing,
# app selection, and bootstrap settings. No .env file is needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_FILE="${1:-examples/bootstrap-profiles/media-compose-standard.yaml}"

# Shift profile arg if it was a YAML file; otherwise use default.
if [[ "$PROFILE_FILE" == *.yaml ]] || [[ "$PROFILE_FILE" == *.yml ]]; then
    shift || true
else
    PROFILE_FILE="examples/bootstrap-profiles/media-compose-standard.yaml"
fi

PROFILE_PATH="$SCRIPT_DIR/$PROFILE_FILE"
if [[ ! -f "$PROFILE_PATH" ]]; then
    echo "ERROR: Profile not found: $PROFILE_PATH" >&2
    exit 1
fi

# Extract values from profile YAML (portable: grep+sed, no python/yq required).
_yaml_val() { grep "$1:" "$PROFILE_PATH" | head -1 | sed "s/.*$1:\s*//" | tr -d '"' | tr -d "'" | xargs; }

CONFIG_ROOT=$(_yaml_val config_root)
MEDIA_ROOT=$(_yaml_val media_root)
DATA_ROOT=$(_yaml_val data_root)
INSTALL_PROFILE=$(_yaml_val install_profile)

if [[ -z "$CONFIG_ROOT" ]] || [[ -z "$MEDIA_ROOT" ]] || [[ -z "$DATA_ROOT" ]]; then
    echo "ERROR: Profile must define storage.config_root, storage.media_root, storage.data_root" >&2
    exit 1
fi

export CONFIG_ROOT MEDIA_ROOT DATA_ROOT
export BOOTSTRAP_PROFILE_FILE="$PROFILE_PATH"

# Map install_profile to compose --profile flag.
# minimal  → just core services (no profile flag needed)
# standard → activates standard-tier services + bootstrap
# full     → activates all services + bootstrap
COMPOSE_PROFILES=()
case "${INSTALL_PROFILE:-standard}" in
    minimal)
        COMPOSE_PROFILES+=(--profile minimal)
        ;;
    standard)
        COMPOSE_PROFILES+=(--profile standard)
        ;;
    full)
        COMPOSE_PROFILES+=(--profile full)
        ;;
    *)
        echo "WARN: Unknown install_profile '${INSTALL_PROFILE}', using standard" >&2
        COMPOSE_PROFILES+=(--profile standard)
        ;;
esac

echo "Deploy: profile=$PROFILE_FILE install_profile=${INSTALL_PROFILE:-standard}"
echo "  CONFIG_ROOT=$CONFIG_ROOT"
echo "  MEDIA_ROOT=$MEDIA_ROOT"
echo "  DATA_ROOT=$DATA_ROOT"

exec docker compose -f "$SCRIPT_DIR/docker/docker-compose.yml" "${COMPOSE_PROFILES[@]}" "$@" up -d
