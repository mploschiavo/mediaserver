#!/usr/bin/env bash
# Integration test: docker compose up → wait for bootstrap → run Playwright.
#
# Usage:
#   bash bin/run-integration-test.sh
#   BOOTSTRAP_PROFILE_FILE=my-profile.yaml bash bin/run-integration-test.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/docker"
PLAYWRIGHT_DIR="$REPO_ROOT/tests/browser"

# Defaults — override via env vars.
: "${BOOTSTRAP_PROFILE_FILE:=$REPO_ROOT/examples/bootstrap-profiles/media-compose-standard.yaml}"
: "${STACK_COMPOSE_EDGE_PORT:=18080}"
: "${STACK_ADMIN_USERNAME:=admin}"
: "${STACK_ADMIN_PASSWORD:=media-dev}"
: "${BOOTSTRAP_TIMEOUT:=180}"

echo "[test] Profile: $BOOTSTRAP_PROFILE_FILE"
echo "[test] Edge port: $STACK_COMPOSE_EDGE_PORT"

# Step 1: Build controller image.
echo "[test] Building controller image..."
PUSH_IMAGE=0 bash "$REPO_ROOT/bin/build-controller-image.sh"

# Step 2: Start the stack.
echo "[test] Starting compose stack..."
cd "$COMPOSE_DIR"
export BOOTSTRAP_PROFILE_FILE STACK_COMPOSE_EDGE_PORT
TRAEFIK_HTTP_PORT="$STACK_COMPOSE_EDGE_PORT" docker compose up -d

# Step 3: Wait for bootstrap to complete.
echo "[test] Waiting for bootstrap (timeout=${BOOTSTRAP_TIMEOUT}s)..."
deadline=$((SECONDS + BOOTSTRAP_TIMEOUT))
while [ "$SECONDS" -lt "$deadline" ]; do
    phase=$(docker exec media-stack-controller wget -qO- http://127.0.0.1:9100/status 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null || echo "")
    if [ "$phase" = "complete" ]; then
        echo "[test] Bootstrap complete."
        break
    elif [ "$phase" = "error" ]; then
        echo "[test] Bootstrap FAILED."
        docker logs media-stack-controller 2>&1 | tail -20
        exit 1
    fi
    sleep 10
done

if [ "$phase" != "complete" ]; then
    echo "[test] Bootstrap timed out after ${BOOTSTRAP_TIMEOUT}s."
    docker logs media-stack-controller 2>&1 | tail -20
    exit 1
fi

# Step 4: Run Playwright browser tests.
echo "[test] Running Playwright browser tests..."
cd "$PLAYWRIGHT_DIR"
npm install --quiet 2>/dev/null || true
STACK_COMPOSE_EDGE_PORT="$STACK_COMPOSE_EDGE_PORT" \
STACK_ADMIN_USERNAME="$STACK_ADMIN_USERNAME" \
STACK_ADMIN_PASSWORD="$STACK_ADMIN_PASSWORD" \
npx playwright test --project=browser --reporter=list

echo "[test] Integration test passed."
