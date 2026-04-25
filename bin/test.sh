#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_PLAYWRIGHT="${RUN_PLAYWRIGHT:-0}"
RUN_API_E2E="${RUN_API_E2E:-0}"
STACK_NODE_IP="${STACK_NODE_IP:-}"
NAMESPACE="${NAMESPACE:-media-stack}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHONPATH="${ROOT_DIR}/src:${ROOT_DIR}:${PYTHONPATH:-}"

pushd "$ROOT_DIR" >/dev/null

bash bin/lib/run-python-cli.sh run_unit_tests_main.py "$@"
bash -n bin/*.sh bin/*/*.sh
"$PYTHON_BIN" -m py_compile \
  src/media_stack/cli/commands/controller_main.py \
  src/media_stack/cli/commands/run_unit_tests_main.py \
  src/media_stack/cli/workflows/unit_test_runner_service.py \
  src/media_stack/services/apps/jellyfin/cli/ensure_jellyfin_controller_main.py
bash bin/utils/validate-bootstrap-config.sh

if [[ "$RUN_PLAYWRIGHT" == "1" ]]; then
  if [[ -z "$STACK_NODE_IP" ]]; then
    echo "[ERR] RUN_PLAYWRIGHT=1 requires STACK_NODE_IP" >&2
    exit 1
  fi
  bash bin/test/run-playwright-smoke.sh "$STACK_NODE_IP" "$NAMESPACE"
else
  echo "[INFO] Skipping Playwright. Set RUN_PLAYWRIGHT=1 STACK_NODE_IP=<IP> to enable."
fi

if [[ "$RUN_API_E2E" == "1" ]]; then
  python3 tests/e2e/api/verify_api_relationships.py --namespace "$NAMESPACE"
else
  echo "[INFO] Skipping API e2e. Set RUN_API_E2E=1 NAMESPACE=<ns> to enable."
fi

popd >/dev/null
