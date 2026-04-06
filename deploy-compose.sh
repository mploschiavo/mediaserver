#!/usr/bin/env bash
# Deploy media stack via Docker Compose — single command.
#
# Follows the same pattern as deploy-k8s.sh:
#   1. Start all services (bootstrap service starts idle)
#   2. Wait for bootstrap service healthy
#   3. Trigger bootstrap via HTTP API
#   4. Poll status until complete
#
# Usage:
#   ./deploy-compose.sh
#   ./deploy-compose.sh --delete   # teardown + redeploy
#   BOOTSTRAP_RUNNER_IMAGE=my-image:tag ./deploy-compose.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker/docker-compose.yml"
BOOTSTRAP_PORT="${BOOTSTRAP_API_PORT:-9100}"

# Handle --delete flag.
if [[ "${1:-}" == "--delete" ]]; then
    echo "Tearing down compose stack..."
    docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    shift || true
fi

echo "Compose deploy: starting services..."
docker compose -f "$COMPOSE_FILE" up -d

# Wait for controller service to be healthy.
echo "  Waiting for controller service..."
for i in $(seq 1 40); do
    HEALTH=$(curl -sf "http://127.0.0.1:${BOOTSTRAP_PORT}/healthz" 2>/dev/null || echo "")
    [[ -n "$HEALTH" ]] && break
    sleep 3
done
if [[ -z "$HEALTH" ]]; then
    echo "  ERROR: Controller service not responding on port ${BOOTSTRAP_PORT} within 120s" >&2
    exit 1
fi
echo "  Controller service ready."

# Trigger bootstrap via HTTP API (same as K8s flow).
echo "  Triggering bootstrap..."
curl -sf -X POST "http://127.0.0.1:${BOOTSTRAP_PORT}/actions/bootstrap" \
    -H "Content-Type: application/json" -d '{}' || true

# Poll status until complete.
echo "  Polling bootstrap status..."
for i in $(seq 1 60); do
    PHASE=$(curl -sf "http://127.0.0.1:${BOOTSTRAP_PORT}/status" 2>/dev/null | \
        python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null || echo "")
    [[ "$PHASE" == "complete" ]] && echo "  Bootstrap: complete" && break
    [[ "$PHASE" == "error" ]] && echo "  Bootstrap: error (check dashboard)" && break
    sleep 10
done

echo ""
echo "Deploy complete."
echo "  Dashboard: http://127.0.0.1:${BOOTSTRAP_PORT}/"
echo "  Homepage:  http://127.0.0.1:80/app/homepage (via Envoy)"
echo "  Trigger:   curl -X POST http://127.0.0.1:${BOOTSTRAP_PORT}/actions/bootstrap"
