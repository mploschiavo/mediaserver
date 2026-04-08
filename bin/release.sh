#!/usr/bin/env bash
set -euo pipefail

# Media Stack Controller — Release Script
#
# Usage:
#   bash bin/release.sh                  # build + push current VERSION
#   bash bin/release.sh 1.2.0            # set version, build, push
#   bash bin/release.sh --dry-run        # show what would happen
#   bash bin/release.sh --build-only     # build locally, don't push

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Registry config
REGISTRY="${REGISTRY:-harbor.iomio.io}"
PROJECT="${REGISTRY_PROJECT:-library}"
IMAGE_NAME="media-stack-controller"

# Parse args
DRY_RUN=false
BUILD_ONLY=false
NEW_VERSION=""

for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=true ;;
    --build-only) BUILD_ONLY=true ;;
    -h|--help)
      echo "Usage: bash bin/release.sh [VERSION] [--dry-run] [--build-only]"
      echo ""
      echo "  VERSION       Set version (e.g. 1.2.0). Default: read from VERSION file."
      echo "  --dry-run     Show what would happen without executing."
      echo "  --build-only  Build images locally, skip push and git tag."
      echo ""
      echo "Environment:"
      echo "  REGISTRY          Container registry (default: harbor.iomio.io)"
      echo "  REGISTRY_PROJECT  Registry project (default: library)"
      exit 0
      ;;
    *)
      if [[ "$arg" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]]; then
        NEW_VERSION="$arg"
      fi
      ;;
  esac
done

# Read or set version
if [[ -n "$NEW_VERSION" ]]; then
  echo "$NEW_VERSION" > "$REPO_ROOT/VERSION"
fi
VERSION=$(cat "$REPO_ROOT/VERSION" | tr -d '[:space:]')
GIT_SHA=$(git -C "$REPO_ROOT" rev-parse --short HEAD)
GIT_SHA_FULL=$(git -C "$REPO_ROOT" rev-parse HEAD)
BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Image tags
PROD_LOCAL="${IMAGE_NAME}:v${VERSION}"
PROD_LATEST="${IMAGE_NAME}:latest"
PROD_REGISTRY="${REGISTRY}/${PROJECT}/${IMAGE_NAME}:v${VERSION}"
PROD_REGISTRY_LATEST="${REGISTRY}/${PROJECT}/${IMAGE_NAME}:latest"

DEV_LOCAL="${IMAGE_NAME}:v${VERSION}-dev"
DEV_LATEST="${IMAGE_NAME}:dev"
DEV_REGISTRY="${REGISTRY}/${PROJECT}/${IMAGE_NAME}:v${VERSION}-dev"
DEV_REGISTRY_LATEST="${REGISTRY}/${PROJECT}/${IMAGE_NAME}:dev"

echo "============================================"
echo "  Media Stack Controller Release"
echo "============================================"
echo "  Version:    v${VERSION}"
echo "  Git SHA:    ${GIT_SHA} (${GIT_SHA_FULL})"
echo "  Build date: ${BUILD_DATE}"
echo "  Registry:   ${REGISTRY}/${PROJECT}"
echo ""
echo "  Production images:"
echo "    ${PROD_REGISTRY}"
echo "    ${PROD_REGISTRY_LATEST}"
echo "  Dev images:"
echo "    ${DEV_REGISTRY}"
echo "    ${DEV_REGISTRY_LATEST}"
echo "============================================"

if $DRY_RUN; then
  echo ""
  echo "[DRY RUN] Would execute:"
  echo "  1. docker build (production) -> ${PROD_LOCAL}, ${PROD_LATEST}"
  echo "  2. docker build (dev)        -> ${DEV_LOCAL}, ${DEV_LATEST}"
  echo "  3. docker tag + push         -> ${PROD_REGISTRY}, ${PROD_REGISTRY_LATEST}"
  echo "  4. docker tag + push         -> ${DEV_REGISTRY}, ${DEV_REGISTRY_LATEST}"
  echo "  5. git tag v${VERSION}"
  echo "  6. Update dist/ YAMLs with pinned image tag"
  exit 0
fi

cd "$REPO_ROOT"

# --- Build production image ---
echo ""
echo "[1/6] Building production image..."
docker build \
  --build-arg VERSION="$VERSION" \
  --build-arg GIT_SHA="$GIT_SHA_FULL" \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  -f docker/controller.Dockerfile \
  -t "$PROD_LOCAL" \
  -t "$PROD_LATEST" \
  .

# --- Build dev image ---
echo ""
echo "[2/6] Building dev image..."
docker build \
  --build-arg VERSION="$VERSION" \
  --build-arg GIT_SHA="$GIT_SHA_FULL" \
  --build-arg BUILD_DATE="$BUILD_DATE" \
  -f docker/controller.dev.Dockerfile \
  -t "$DEV_LOCAL" \
  -t "$DEV_LATEST" \
  .

if $BUILD_ONLY; then
  echo ""
  echo "[DONE] Images built locally. Skipping push and tag."
  echo "  ${PROD_LOCAL}"
  echo "  ${DEV_LOCAL}"
  exit 0
fi

# --- Tag for registry ---
echo ""
echo "[3/6] Tagging for registry..."
docker tag "$PROD_LOCAL" "$PROD_REGISTRY"
docker tag "$PROD_LATEST" "$PROD_REGISTRY_LATEST"
docker tag "$DEV_LOCAL" "$DEV_REGISTRY"
docker tag "$DEV_LATEST" "$DEV_REGISTRY_LATEST"

# --- Push ---
echo ""
echo "[4/6] Pushing to ${REGISTRY}..."
docker push "$PROD_REGISTRY"
docker push "$PROD_REGISTRY_LATEST"
docker push "$DEV_REGISTRY"
docker push "$DEV_REGISTRY_LATEST"

# --- Update dist/ YAMLs ---
echo ""
echo "[5/6] Updating dist/ YAMLs with pinned image tag..."
PINNED_IMAGE="${REGISTRY}/${PROJECT}/${IMAGE_NAME}:v${VERSION}"

# Update docker-compose dist
if [[ -f dist/docker-compose.yml ]]; then
  sed -i "s|192\.168\.1\.60:30002/library/${IMAGE_NAME}:[^ ]*|${PINNED_IMAGE}|g" dist/docker-compose.yml
  sed -i "s|harbor\.iomio\.io/library/${IMAGE_NAME}:[^ ]*|${PINNED_IMAGE}|g" dist/docker-compose.yml
  echo "  Updated dist/docker-compose.yml"
fi

# Update k8s dist
if [[ -f dist/k8s-deploy.yaml ]]; then
  sed -i "s|192\.168\.1\.60:30002/library/${IMAGE_NAME}:[^ ]*|${PINNED_IMAGE}|g" dist/k8s-deploy.yaml
  sed -i "s|harbor\.iomio\.io/library/${IMAGE_NAME}:[^ ]*|${PINNED_IMAGE}|g" dist/k8s-deploy.yaml
  echo "  Updated dist/k8s-deploy.yaml"
fi

# Update docker-compose.yml (source)
if [[ -f docker/docker-compose.yml ]]; then
  sed -i "s|192\.168\.1\.60:30002/library/${IMAGE_NAME}:[^ }]*|${PINNED_IMAGE}|g" docker/docker-compose.yml
  echo "  Updated docker/docker-compose.yml"
fi

# --- Git tag ---
echo ""
echo "[6/6] Creating git tag v${VERSION}..."
if git -C "$REPO_ROOT" tag -l "v${VERSION}" | grep -q "v${VERSION}"; then
  echo "  Tag v${VERSION} already exists. Skipping."
else
  git -C "$REPO_ROOT" tag -a "v${VERSION}" -m "Release v${VERSION}"
  echo "  Tagged v${VERSION}"
  echo "  Push tag with: git push origin v${VERSION}"
fi

echo ""
echo "============================================"
echo "  Release v${VERSION} complete!"
echo "============================================"
echo ""
echo "  Pull commands:"
echo "    docker pull ${PROD_REGISTRY}"
echo "    docker pull ${DEV_REGISTRY}"
echo ""
echo "  One-liner deploy (Docker Compose):"
echo "    curl -sL https://raw.githubusercontent.com/mploschiavo/mediaserver/v${VERSION}/dist/docker-compose.yml | docker compose -f - up -d"
echo ""
echo "  One-liner deploy (Kubernetes):"
echo "    kubectl apply -f https://raw.githubusercontent.com/mploschiavo/mediaserver/v${VERSION}/dist/k8s-deploy.yaml"
echo ""
echo "  Push git tag:"
echo "    git push origin v${VERSION}"
