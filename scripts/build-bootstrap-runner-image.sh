#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERFILE="${DOCKERFILE:-$ROOT_DIR/docker/bootstrap-runner.Dockerfile}"
IMAGE="${BOOTSTRAP_RUNNER_IMAGE:-192.168.1.60:30002/library/media-stack-bootstrap-runner:latest}"
PUSH_IMAGE="${PUSH_IMAGE:-1}"
ENGINE="${CONTAINER_ENGINE:-}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/build-bootstrap-runner-image.sh [--image IMAGE] [--push|--no-push] [--engine docker|podman]

Builds the bootstrap runner image used by k8s/bootstrap-job.yaml and related CronJobs.

Defaults:
  IMAGE: 192.168.1.60:30002/library/media-stack-bootstrap-runner:latest
  PUSH: enabled

Environment overrides:
  BOOTSTRAP_RUNNER_IMAGE
  PUSH_IMAGE (1 or 0)
  CONTAINER_ENGINE (docker or podman)
USAGE
}

choose_engine() {
  if [[ -n "$ENGINE" ]]; then
    echo "$ENGINE"
    return 0
  fi
  if command -v docker >/dev/null 2>&1; then
    echo docker
    return 0
  fi
  if command -v podman >/dev/null 2>&1; then
    echo podman
    return 0
  fi
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --image)
      IMAGE="${2:-}"
      shift 2
      ;;
    --push)
      PUSH_IMAGE=1
      shift
      ;;
    --no-push)
      PUSH_IMAGE=0
      shift
      ;;
    --engine)
      ENGINE="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$IMAGE" ]]; then
  echo "Image reference cannot be empty" >&2
  exit 1
fi

if [[ ! -f "$DOCKERFILE" ]]; then
  echo "Dockerfile not found: $DOCKERFILE" >&2
  exit 1
fi

ENGINE_BIN="$(choose_engine)" || {
  echo "Neither docker nor podman was found in PATH" >&2
  exit 1
}

set -x
"$ENGINE_BIN" build -f "$DOCKERFILE" -t "$IMAGE" "$ROOT_DIR"
if [[ "$PUSH_IMAGE" == "1" ]]; then
  "$ENGINE_BIN" push "$IMAGE"
fi
set +x

echo "Built bootstrap runner image: $IMAGE"
if [[ "$PUSH_IMAGE" == "1" ]]; then
  echo "Pushed bootstrap runner image: $IMAGE"
fi
