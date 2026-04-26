#!/usr/bin/env bash
# Run the lychee link-checker over docs/.
# ADR-0001 Phase 15. Wired into CI as a separate gate (parallel to the
# Phase 12-F console-scripts smoke and the Phase 14 unit-tests).
#
# Modes:
#   bash bin/ops/lychee.sh --offline   # CI default — relative + anchor checks only
#   bash bin/ops/lychee.sh             # full network mode — also resolves http(s)
#                                       # URLs (slower; subject to flake)
#
# Allowlist / config: .lychee.toml at repo root pins ignore-rules,
# accepted status codes, timeouts, and excluded paths.
#
# Exit codes:
#   0 — all links resolved
#   non-zero — broken links, OR lychee binary not installed (treated as
#              an environment error so CI fails loudly).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$REPO_ROOT"

if ! command -v lychee >/dev/null 2>&1; then
  echo "lychee not on PATH — install via 'cargo install lychee' or 'brew install lychee'" >&2
  echo "in CI: pinned via the curl-bash installer in the docs job (see .github/workflows/ci.yml)" >&2
  exit 127
fi

# Default mode is --offline so the CI gate stays deterministic. Pass
# any args after `--` straight through to lychee for one-off probes.
mode="${1:---offline}"
shift || true

# --no-progress quiets the progress bar in CI logs but still emits per-link results.
exec lychee "$mode" \
  --config "$REPO_ROOT/.lychee.toml" \
  --no-progress \
  "$@" \
  "docs/**/*.md" \
  "README.md" \
  "CONTRIBUTING.md"
