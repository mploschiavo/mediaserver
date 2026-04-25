#!/usr/bin/env bash
# verify-fresh-install.sh — runtime probe of every promise in
# contracts/promises.yaml.
#
# Two modes:
#
#   ./bin/verify-fresh-install.sh           — probe ONLY (current state)
#       Just hits each promise's probe against the live stack. Fast,
#       safe, no side effects. Use to confirm your dev stack is
#       healthy or after manually triggering bootstrap.
#
#   ./bin/verify-fresh-install.sh --wipe    — full clean install + probe
#       Runs ``compose down -v --remove-orphans``, wipes config/
#       (preserving defaults), wipes data/ and media/, brings the
#       stack back up via ``compose up -d``, waits for the controller's
#       initial bootstrap to complete, then probes every promise.
#       This is the definitive "does a fresh install actually work
#       OTB" test. DESTRUCTIVE — confirms before wiping unless --yes.
#
# Exit codes:
#   0 — every promise probe passed
#   1 — at least one probe failed
#   2 — bootstrap timed out / stack not reachable
#
# Configurable via env:
#   COMPOSE_FILE        default: docker/docker-compose.yml (relative to repo root)
#   CONFIG_ROOT         default: ../config
#   BOOTSTRAP_TIMEOUT   default: 600 (seconds to wait for controller bootstrap)
#   PROMISE_TIMEOUT     default: 15  (seconds per HTTP probe)
#   CONTROLLER_URL      default: http://localhost:9100
#   ADMIN_USER          default: admin
#   ADMIN_PASS          default: admin
#
# This script delegates the hard work to a Python helper that
# evaluates each promise's ``assert`` expression. Bash here just
# orchestrates wipe/up/wait/dispatch.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

COMPOSE_FILE="${COMPOSE_FILE:-docker/docker-compose.yml}"
CONFIG_ROOT="${CONFIG_ROOT:-../config}"
BOOTSTRAP_TIMEOUT="${BOOTSTRAP_TIMEOUT:-600}"
CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:9100}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin}"

WIPE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --wipe) WIPE=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --help|-h)
      sed -n '2,/^set -e/p' "$0" | sed 's/^# //; s/^#//'
      exit 0
      ;;
  esac
done

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

if [ "$WIPE" -eq 1 ]; then
  if [ "$ASSUME_YES" -ne 1 ]; then
    printf 'About to:\n  - docker compose down -v --remove-orphans\n  - rm -rf %s/* (keeps defaults/)\n  - rm -rf data/ media/ contents\nProceed? [y/N] ' "$CONFIG_ROOT"
    read -r ans
    case "$ans" in y|Y|yes|YES) ;; *) log "aborted"; exit 0 ;; esac
  fi

  log "compose down -v --remove-orphans"
  docker compose -f "$COMPOSE_FILE" down -v --remove-orphans >/dev/null 2>&1 || true

  log "wiping bind-mount state"
  docker run --rm \
    -v "$REPO/config:/cfg" \
    -v "$REPO/data:/dat" \
    -v "$REPO/media:/med" \
    alpine sh -c '
      cd /cfg && find . -mindepth 1 -maxdepth 1 ! -name defaults -exec rm -rf {} +
      cd /dat && rm -rf ./*
      cd /med && rm -rf ./*
    ' >/dev/null

  log "compose up -d (cold start)"
  docker compose -f "$COMPOSE_FILE" up -d >/dev/null

  log "waiting for controller (timeout ${BOOTSTRAP_TIMEOUT}s)"
  deadline=$(( $(date +%s) + BOOTSTRAP_TIMEOUT ))
  until curl -sf "$CONTROLLER_URL/healthz" >/dev/null 2>&1; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "controller never came up — exit 2"
      exit 2
    fi
    sleep 5
  done

  log "waiting for initial bootstrap to complete"
  until docker compose -f "$COMPOSE_FILE" logs media-stack-controller 2>&1 \
        | grep -q "Initial bootstrap complete"; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "bootstrap didn't complete in time — exit 2"
      exit 2
    fi
    sleep 8
  done

  # Give the post-phase ensure-* jobs a beat to finish.
  sleep 15
fi

log "running promise probes"
exec media-stack-probe-promises \
  --compose-file "$COMPOSE_FILE" \
  --controller-url "$CONTROLLER_URL" \
  --admin-user "$ADMIN_USER" \
  --admin-pass "$ADMIN_PASS"
