#!/usr/bin/env bash
# Generate an SPDX SBOM for the controller image using syft.
#
# Usage:
#   bin/generate-sbom.sh                                   # latest local tag
#   bin/generate-sbom.sh harbor.iomio.io/.../ctrl:v1.0.65  # specific image
#   IMAGE=... bin/generate-sbom.sh                         # env var form
#
# Output: artifacts/sbom/<sanitized-image-name>.spdx.json
#
# Requires: syft (https://github.com/anchore/syft).
# Install:   curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh \
#              | sh -s -- -b ~/.local/bin
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${1:-${IMAGE:-harbor.iomio.io/library/media-stack-controller:latest}}"

if ! command -v syft >/dev/null 2>&1; then
  echo "[ERR] syft not found. See install link in script header." >&2
  exit 2
fi

OUT_DIR="${ROOT_DIR}/artifacts/sbom"
mkdir -p "$OUT_DIR"

# Sanitize image ref → filename (replace / and : with -).
SAFE_NAME="$(printf '%s' "$IMAGE" | tr '/:' '--')"
OUT_FILE="${OUT_DIR}/${SAFE_NAME}.spdx.json"

echo "[INFO] Generating SBOM for $IMAGE"
syft "$IMAGE" -o spdx-json > "$OUT_FILE"
echo "[OK] SBOM written: $OUT_FILE ($(wc -c <"$OUT_FILE") bytes)"

# Quick summary.
python3 - "$OUT_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
pkgs = d.get("packages", [])
by_type = {}
for p in pkgs:
    ptype = (p.get("externalRefs") or [{}])[0].get("referenceType", "unknown")
    by_type[ptype] = by_type.get(ptype, 0) + 1
print(f"[INFO] {len(pkgs)} packages cataloged")
for t, c in sorted(by_type.items(), key=lambda x: -x[1])[:5]:
    print(f"         {c:>5}  {t}")
PY
