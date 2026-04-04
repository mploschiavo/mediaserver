#!/usr/bin/env bash
# Media Automation Stack deploy — reads profile YAML and runs docker compose.
#
# Usage:
#   ./deploy.sh                                          # default profile
#   ./deploy.sh examples/bootstrap-profiles/custom.yaml  # custom profile
#   ./deploy.sh profile.yaml --profile bootstrap         # with bootstrap
#
# The profile YAML is the single source of truth for storage paths, routing,
# and bootstrap settings. No .env file is needed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_FILE="${1:-examples/bootstrap-profiles/media-compose-standard.yaml}"

# Shift profile arg if it was a YAML file.
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

# Extract storage paths from profile YAML (portable: uses grep+sed, no python/yq required).
CONFIG_ROOT=$(grep 'config_root:' "$PROFILE_PATH" | head -1 | sed 's/.*config_root:\s*//' | tr -d '"' | tr -d "'")
MEDIA_ROOT=$(grep 'media_root:' "$PROFILE_PATH" | head -1 | sed 's/.*media_root:\s*//' | tr -d '"' | tr -d "'")
DATA_ROOT=$(grep 'data_root:' "$PROFILE_PATH" | head -1 | sed 's/.*data_root:\s*//' | tr -d '"' | tr -d "'")

if [[ -z "$CONFIG_ROOT" ]] || [[ -z "$MEDIA_ROOT" ]] || [[ -z "$DATA_ROOT" ]]; then
    echo "ERROR: Profile must define storage.config_root, storage.media_root, storage.data_root" >&2
    exit 1
fi

export CONFIG_ROOT MEDIA_ROOT DATA_ROOT
export BOOTSTRAP_PROFILE_FILE="$PROFILE_PATH"

# Pass remaining args (e.g. --profile bootstrap, -d, etc.) to docker compose.
exec docker compose -f "$SCRIPT_DIR/docker/docker-compose.yml" "$@" up -d
