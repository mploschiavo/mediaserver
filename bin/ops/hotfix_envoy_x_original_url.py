"""Live-cluster hotfix: add X-Original-URL to the Lua envoy_on_request
that the controller's earlier splice already injected.

Why X-Original-URL specifically:
  Authelia's legacy /api/verify endpoint computes the post-login `rd`
  from the URL it sees on the auth check. It does NOT honor
  X-Forwarded-Uri on this endpoint — only X-Original-URL overrides
  the request path. Production logs (2026-04-25) showed Authelia
  building the rd from the ext_authz path_prefix itself
  (`/api/verify?rd=...&authz_path=/foo`) even when X-Forwarded-Uri
  was set, looping the user back through /api/verify forever.

Why text-based, not YAML-based:
  The live envoy.yaml has unescaped double quotes inside a YAML
  double-quoted scalar (artifact of the controller's earlier splice
  using str.replace without re-escaping). Envoy's parser accepts it;
  PyYAML rejects it. So we do a targeted text substitution rather
  than parse + re-emit.

Usage:
    kubectl -n media-stack cp bin/ops/hotfix_envoy_x_original_url.py \\
        deploy/media-stack-controller:/tmp/hotfix2.py
    kubectl -n media-stack exec deploy/media-stack-controller -- \\
        python3 /tmp/hotfix2.py
    kubectl -n media-stack rollout restart deploy/envoy
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ENVOY_YAML = Path("/srv-config/envoy/envoy.yaml")

# The LAST line of the existing auth-request Lua splice. We anchor on
# this rather than on a byte range so re-running the script is a
# no-op (idempotent: if x-original-url is already there, we bail).
ANCHOR = (
    '"x-forwarded-method", handle:headers():get(":method") or "GET")'
    '\\nend'
)

# What we insert: a new replace() call right before `end`. Mirrors
# the malformed-but-Envoy-accepted quoting pattern of the existing
# splice — bare double quotes inside the YAML double-quoted scalar.
INSERT = (
    '"x-forwarded-method", handle:headers():get(":method") or "GET")'
    '\\n  handle:headers():replace("x-original-url", "https://" .. '
    '(handle:headers():get(":authority") or "") .. '
    '(handle:headers():get(":path") or "/"))'
    '\\nend'
)


def main() -> int:
    if not ENVOY_YAML.is_file():
        print(f"[hotfix2] not found: {ENVOY_YAML}", file=sys.stderr)
        return 1
    text = ENVOY_YAML.read_text(encoding="utf-8")

    if "x-original-url" in text.lower():
        print("[hotfix2] already patched (x-original-url present)")
        return 0

    if ANCHOR not in text:
        print("[hotfix2] could not find the auth-request Lua anchor — "
              "the live envoy.yaml does not match the expected shape. "
              "Refusing to patch blindly.", file=sys.stderr)
        return 2

    backup = ENVOY_YAML.with_suffix(".yaml.pre-x-original-url")
    shutil.copy2(ENVOY_YAML, backup)
    new_text = text.replace(ANCHOR, INSERT, 1)
    ENVOY_YAML.write_text(new_text, encoding="utf-8")

    print(f"[hotfix2] inserted x-original-url replace() into envoy_on_request")
    print(f"[hotfix2] backup: {backup}")
    print("[hotfix2] now: kubectl -n media-stack rollout restart deploy/envoy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
