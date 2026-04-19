#!/usr/bin/env bash
# Sign the controller image with cosign and optionally attest an SBOM.
#
# Two signing modes:
#   1. Keyless (preferred for CI) — uses an OIDC identity from the CI
#      environment (GitHub Actions, GitLab, etc.) via the Fulcio CA.
#      Activated when COSIGN_EXPERIMENTAL=1 AND no COSIGN_KEY is set.
#   2. Keyful — requires COSIGN_KEY pointing at a cosign-generated
#      private key file (cosign.key) + COSIGN_PASSWORD.
#
# Usage:
#   IMAGE=... bin/sign-image.sh                      # sign image only
#   IMAGE=... bin/sign-image.sh --with-sbom          # sign + attest SBOM
#
# Requires: cosign (https://docs.sigstore.dev/cosign/installation/).
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${IMAGE:-harbor.iomio.io/library/media-stack-controller:latest}"
WITH_SBOM=0
for arg in "$@"; do
  case "$arg" in
    --with-sbom) WITH_SBOM=1 ;;
    -h|--help)
      sed -n '1,/^set -/p' "$0" | sed '$d' | sed 's/^# \?//'
      exit 0
      ;;
  esac
done

if ! command -v cosign >/dev/null 2>&1; then
  echo "[ERR] cosign not found. Install: https://docs.sigstore.dev/cosign/installation/" >&2
  exit 2
fi

MODE="unknown"
if [[ -n "${COSIGN_KEY:-}" && -f "${COSIGN_KEY}" ]]; then
  MODE="keyful"
elif [[ "${COSIGN_EXPERIMENTAL:-0}" == "1" ]]; then
  MODE="keyless"
fi

if [[ "$MODE" == "unknown" ]]; then
  cat >&2 <<EOF
[ERR] No signing mode configured.
      Keyful: export COSIGN_KEY=./cosign.key COSIGN_PASSWORD=...
      Keyless: export COSIGN_EXPERIMENTAL=1 (CI OIDC required)
EOF
  exit 2
fi

echo "[INFO] Signing $IMAGE (mode=$MODE)"
if [[ "$MODE" == "keyful" ]]; then
  cosign sign --key "$COSIGN_KEY" --yes "$IMAGE"
else
  cosign sign --yes "$IMAGE"
fi
echo "[OK] Image signature pushed to registry"

if [[ "$WITH_SBOM" == "1" ]]; then
  SAFE_NAME="$(printf '%s' "$IMAGE" | tr '/:' '--')"
  SBOM_PATH="${ROOT_DIR}/artifacts/sbom/${SAFE_NAME}.spdx.json"
  if [[ ! -f "$SBOM_PATH" ]]; then
    echo "[INFO] SBOM not found at $SBOM_PATH — generating now"
    "$ROOT_DIR/bin/generate-sbom.sh" "$IMAGE"
  fi
  echo "[INFO] Attesting SBOM → $IMAGE"
  if [[ "$MODE" == "keyful" ]]; then
    cosign attest --key "$COSIGN_KEY" --yes \
      --predicate "$SBOM_PATH" --type spdxjson "$IMAGE"
  else
    cosign attest --yes \
      --predicate "$SBOM_PATH" --type spdxjson "$IMAGE"
  fi
  echo "[OK] SBOM attestation pushed"
fi
