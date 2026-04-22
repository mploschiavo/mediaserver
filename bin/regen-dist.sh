#!/usr/bin/env bash
# Regenerate dist/ single-file deployment bundles from source.
#
# dist/docker-compose.yml — docker/docker-compose.yml + distribution header
# dist/k8s-deploy.yaml    — kubectl kustomize k8s/ + distribution header
#
# Run after bumping image tags in docker/docker-compose.yml or
# k8s/kustomization.yaml so the one-file bundles users download stay
# in lockstep with the sources kubectl/compose apply from a checkout.
set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
DIST_DIR="${REPO_ROOT}/dist"

mkdir -p "${DIST_DIR}"

# --- Pre-flight: image-version parity ---
# Catches the bug class where someone bumps VERSION but a sed in
# their release script misses some manifest, leaving stale image
# refs that would deploy an OLD controller. The
# ``ControllerImageVersionParity`` ratchet (Batch 4, v1.0.119)
# scans every YAML for ``media-stack-controller:vX.Y.Z`` refs and
# fails if any disagree with the canonical VERSION file.
#
# Skipped if pytest isn't available — the check still runs in CI.
if [ -z "${SKIP_VERSION_CHECK:-}" ] && command -v python3 >/dev/null && \
   python3 -c "import pytest" 2>/dev/null; then
  echo "==> Pre-flight: image-version parity..."
  if ! python3 -m pytest -q --no-header \
        "${REPO_ROOT}/tests/unit/test_v1_0_119_batch4_ratchets.py::ControllerImageVersionParity" \
        2>&1 | tail -3; then
    echo "ERROR: image refs disagree with VERSION file."
    echo "Set SKIP_VERSION_CHECK=1 to bypass (not recommended)."
    exit 1
  fi
fi

echo "Regenerating dist/docker-compose.yml..."
{
    cat <<'EOF'
# Media Automation Stack — Docker Compose Single-File Deploy (distribution snapshot)
#
# Deploy:   docker compose -f docker-compose.yml up -d
# Status:   docker compose -f docker-compose.yml ps
# Dashboard: http://localhost:9100
# Teardown: docker compose -f docker-compose.yml down -v
#
# Prerequisites: Docker Engine 24+ with Compose V2.
#
# NOTE: This file expects to run from a repo checkout. Bind mounts reference
# ../config, ../contracts, ../examples. If you are using this file standalone,
# clone the repo and run from the docker/ directory instead:
#   git clone https://github.com/mploschiavo/mediaserver.git && cd mediaserver/docker
#   docker compose up -d
#
# Regenerate this file after editing docker/docker-compose.yml:
#   bin/regen-dist.sh
EOF
    cat "${REPO_ROOT}/docker/docker-compose.yml"
} > "${DIST_DIR}/docker-compose.yml"

echo "Regenerating dist/k8s-deploy.yaml..."
{
    cat <<'EOF'
# Media Automation Stack — Kubernetes Single-File Deploy
#
# Deploy:   kubectl apply -f k8s-deploy.yaml
# Status:   kubectl get pods -n media-stack
# Dashboard: kubectl port-forward -n media-stack svc/media-stack-controller 9100:9100
# Teardown: kubectl delete -f k8s-deploy.yaml
#
# Prerequisites: Kubernetes 1.25+ with a default StorageClass
# More info: https://github.com/mploschiavo/mediaserver
#
# Generated from k8s/ manifests (kubectl kustomize k8s/).
# Do NOT hand-edit — regenerate with `bin/regen-dist.sh`.
EOF
    kubectl kustomize "${REPO_ROOT}/k8s/"
} > "${DIST_DIR}/k8s-deploy.yaml"

echo ""
echo "dist/ regenerated:"
wc -l "${DIST_DIR}/docker-compose.yml" "${DIST_DIR}/k8s-deploy.yaml"
controller_tag_k8s="$(grep -oE 'media-stack-controller:v[0-9.]+' "${DIST_DIR}/k8s-deploy.yaml" | head -1)"
controller_tag_compose="$(grep -oE 'media-stack-controller:v[0-9.]+' "${DIST_DIR}/docker-compose.yml" | head -1)"
echo "  compose controller image: ${controller_tag_compose}"
echo "  k8s controller image:     ${controller_tag_k8s}"
