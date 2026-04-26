#!/usr/bin/env bash
# Regenerate auto-generated subtrees of docs/reference/ from canonical
# sources. ADR-0001 Phase 15.
#
# Sources -> outputs:
#   contracts/api/openapi.yaml   -> docs/reference/api/<tag>.md + index.md
#   pyproject.toml [project.scripts] -> docs/reference/cli/<name>.md + index.md
#   contracts/services/*.yaml    -> docs/reference/services.md
#
# Idempotent: re-running on an unchanged source tree leaves
# docs/reference/ byte-identical (CI assertion: `git diff --exit-code
# docs/reference/` after this script).
#
# Usage:
#   bash bin/ops/generate-reference-docs.sh           # human-readable output
#   bash bin/ops/generate-reference-docs.sh --quiet   # CI-friendly
#   bash bin/ops/generate-reference-docs.sh --json    # machine-readable summary

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

if ! python3 -c "import yaml, tomllib" >/dev/null 2>&1; then
  echo "missing deps: PyYAML and Python >=3.11 (tomllib) required" >&2
  exit 2
fi

exec python3 "$SCRIPT_DIR/generate_reference_docs.py" "$@"
