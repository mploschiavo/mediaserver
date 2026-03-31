#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIAGRAM_DIR="${1:-$ROOT_DIR/docs/diagrams}"

usage() {
  cat <<'EOF'
Usage:
  scripts/render-architecture-diagrams.sh [DIAGRAM_DIR]

Description:
  Renders all .mmd files in docs/diagrams to both SVG and PNG.
  Renderer priority:
  1) mermaid-cli (`mmdc`) if installed
  2) `npx @mermaid-js/mermaid-cli`
  3) Kroki API via curl
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ ! -d "$DIAGRAM_DIR" ]]; then
  echo "[ERR] Diagram directory not found: $DIAGRAM_DIR" >&2
  exit 1
fi

shopt -s nullglob
mmd_files=("$DIAGRAM_DIR"/*.mmd)
if [[ "${#mmd_files[@]}" -eq 0 ]]; then
  echo "[ERR] No .mmd files found in $DIAGRAM_DIR" >&2
  exit 1
fi

render_with_mmdc() {
  local input="$1"
  local svg_out="${input%.mmd}.svg"
  local png_out="${input%.mmd}.png"
  "${MMDC_CMD[@]}" -i "$input" -o "$svg_out" >/dev/null
  "${MMDC_CMD[@]}" -i "$input" -o "$png_out" >/dev/null
}

render_with_kroki() {
  local input="$1"
  local svg_out="${input%.mmd}.svg"
  local png_out="${input%.mmd}.png"

  curl -fsS \
    --retry 8 \
    --retry-delay 2 \
    --retry-all-errors \
    --connect-timeout 10 \
    --max-time 120 \
    -H "Content-Type: text/plain" \
    --data-binary "@$input" \
    "https://kroki.io/mermaid/svg" > "$svg_out"

  curl -fsS \
    --retry 8 \
    --retry-delay 2 \
    --retry-all-errors \
    --connect-timeout 10 \
    --max-time 120 \
    -H "Content-Type: text/plain" \
    --data-binary "@$input" \
    "https://kroki.io/mermaid/png" > "$png_out"
}

MMDC_CMD=()
if command -v mmdc >/dev/null 2>&1; then
  MMDC_CMD=(mmdc)
  echo "[INFO] Using local renderer: mmdc"
  for file in "${mmd_files[@]}"; do
    echo "[INFO] Rendering $(basename "$file")"
    render_with_mmdc "$file"
  done
elif command -v npx >/dev/null 2>&1; then
  MMDC_CMD=(npx -y @mermaid-js/mermaid-cli@10.9.1)
  echo "[INFO] Using local renderer: npx @mermaid-js/mermaid-cli"
  for file in "${mmd_files[@]}"; do
    echo "[INFO] Rendering $(basename "$file")"
    render_with_mmdc "$file"
  done
elif command -v curl >/dev/null 2>&1; then
  echo "[INFO] Using remote renderer: kroki.io"
  for file in "${mmd_files[@]}"; do
    echo "[INFO] Rendering $(basename "$file")"
    render_with_kroki "$file"
  done
else
  echo "[ERR] Neither mmdc nor curl is available." >&2
  exit 1
fi

echo "[OK] Rendered ${#mmd_files[@]} diagram(s) to SVG and PNG in $DIAGRAM_DIR"
