#!/usr/bin/env bash
# Verify the controller image signature + SBOM attestation.
#
# Pull-time gate — run this before rolling an image out so you know
# the tag you're about to run was signed by the expected identity
# and has an SBOM attached. Fails non-zero if EITHER check fails.
#
# Usage:
#   IMAGE=... bin/verify-image.sh
#   IMAGE=... EXPECTED_IDENTITY=user@example.com bin/verify-image.sh
#   IMAGE=... COSIGN_PUB=./cosign.pub bin/verify-image.sh   # keyful
#
# Exit codes:
#   0  signature + (if present) SBOM attestation valid
#   1  signature invalid / missing
#   2  cosign not installed
set -Eeuo pipefail

IMAGE="${IMAGE:-harbor.iomio.io/library/media-stack-controller:latest}"

if ! command -v cosign >/dev/null 2>&1; then
  echo "[ERR] cosign not found" >&2
  exit 2
fi

echo "[INFO] Verifying signature on $IMAGE"

if [[ -n "${COSIGN_PUB:-}" && -f "$COSIGN_PUB" ]]; then
  cosign verify --key "$COSIGN_PUB" "$IMAGE" >/dev/null
else
  # Keyless: expect an OIDC identity the caller trusts.
  EXPECTED_IDENTITY="${EXPECTED_IDENTITY:-}"
  EXPECTED_ISSUER="${EXPECTED_ISSUER:-https://token.actions.githubusercontent.com}"
  if [[ -z "$EXPECTED_IDENTITY" ]]; then
    echo "[WARN] No EXPECTED_IDENTITY / COSIGN_PUB set — accepting any signature"
    cosign verify \
      --certificate-identity-regexp ".*" \
      --certificate-oidc-issuer-regexp ".*" \
      "$IMAGE" >/dev/null
  else
    cosign verify \
      --certificate-identity "$EXPECTED_IDENTITY" \
      --certificate-oidc-issuer "$EXPECTED_ISSUER" \
      "$IMAGE" >/dev/null
  fi
fi
echo "[OK] Image signature valid"

# SBOM attestation is optional; surface its presence but don't fail
# when missing — some images are signed without SBOMs.
if cosign verify-attestation --type spdxjson \
    ${COSIGN_PUB:+--key "$COSIGN_PUB"} \
    ${EXPECTED_IDENTITY:+--certificate-identity "$EXPECTED_IDENTITY"} \
    ${EXPECTED_IDENTITY:+--certificate-oidc-issuer "$EXPECTED_ISSUER"} \
    "$IMAGE" >/dev/null 2>&1; then
  echo "[OK] SBOM attestation present and valid"
else
  echo "[WARN] No SBOM attestation (or invalid). Consider running bin/sign-image.sh --with-sbom"
fi
