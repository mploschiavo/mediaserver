#!/usr/bin/env bash
# Recapture API response fixtures for every parameter-free GET endpoint
# the OpenAPI spec documents. Used after a wide change (schema bump,
# batch handler edit) to refresh the contract-test fixture pool.
#
# Per-endpoint capture is documented in tests/fixtures/api_responses/CAPTURE.md.
# This script automates the bulk case but uses the same kubectl exec
# probe so the captured shape is exactly what a live consumer sees.
#
# Endpoints with path templates (e.g. /api/users/{id}) are SKIPPED —
# they need representative parameters chosen by hand. The contract
# test's ENDPOINTS map is where you wire those up explicitly.
#
# Usage:
#   bash tools/recapture-all-fixtures.sh          # default: media-stack ns
#   NS=other-ns bash tools/recapture-all-fixtures.sh

set -euo pipefail

NS="${NS:-media-stack}"
SPEC="src/media_stack/api/openapi.yaml"
OUT_DIR="tests/fixtures/api_responses"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl required" >&2
  exit 1
fi
if [ ! -f "$SPEC" ]; then
  echo "$SPEC not found — run from repo root" >&2
  exit 1
fi

CTRL_POD="$(kubectl -n "$NS" get pod -l app=media-stack-controller \
                              -o jsonpath='{.items[0].metadata.name}')"
if [ -z "$CTRL_POD" ]; then
  echo "no controller pod in namespace $NS" >&2
  exit 1
fi
echo "controller: $NS/$CTRL_POD"

# Walk paths block of openapi.yaml. Match `^  /api/foo:` (two-space
# indent, no path params). Strip the trailing colon.
mapfile -t PATHS < <(
  awk '
    /^paths:/                           { in_paths=1; next }
    in_paths && /^[A-Za-z]/             { in_paths=0 }
    in_paths && /^  \/api\/[^{}]+:[[:space:]]*$/ {
      gsub(/^[[:space:]]+/, "")
      gsub(/:[[:space:]]*$/, "")
      print
    }
  ' "$SPEC"
)

mkdir -p "$OUT_DIR"
ok=0; fail=0
for path in "${PATHS[@]}"; do
  # Filename: /api/foo/bar -> foo_bar.json
  fname="$(echo "${path#/api/}" | tr '/' '_').json"
  out="$OUT_DIR/$fname"
  body="$(kubectl -n "$NS" exec "$CTRL_POD" -- python3 -c "
import urllib.request, json, sys
try:
    r = urllib.request.urlopen(
        urllib.request.Request('http://localhost:9100${path}',
                               headers={'Remote-User':'admin'}),
        timeout=15,
    )
    body = json.loads(r.read())
    # Skip non-JSON-object responses (file downloads, raw text). The
    # contract validator only handles structured responses.
    if not isinstance(body, (dict, list)):
        print('SKIP non-json-object', file=sys.stderr); sys.exit(2)
    print(json.dumps(body, indent=2, sort_keys=True))
except Exception as e:
    print(f'ERR {type(e).__name__}: {e}', file=sys.stderr); sys.exit(1)
" 2>&1)" || { echo "  ✗ $path ($body)"; fail=$((fail+1)); continue; }
  echo "$body" > "$out"
  echo "  ✓ $fname ($(wc -c < "$out") bytes) <- $path"
  ok=$((ok+1))
done
echo
echo "captured: $ok ok, $fail failed/skipped"
