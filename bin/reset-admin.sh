#!/usr/bin/env bash
# Reset the stack admin credential. Run inside the controller pod.
#
# K8s:
#   kubectl -n media-stack exec -it deploy/media-stack-controller -- \
#     bin/reset-admin.sh --username admin --prompt
#
# Compose:
#   docker exec -it media-stack-controller \
#     bin/reset-admin.sh --username admin --prompt
#
# See --help for flags (--password, --prompt, --password-stdin).
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/lib/run-python-cli.sh" reset_admin_main.py "$@"
