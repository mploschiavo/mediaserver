"""Recovery hotfix for the broken live envoy.yaml.

Two surgical changes:

1. Strip the malformed `function envoy_on_request(handle)` block from
   inside the Lua inline_code scalar. The controller's earlier
   str.replace splice inserted bare `"` characters into a YAML
   double-quoted scalar; yaml-cpp errors at "end of map not found"
   at the first unescaped quote (`"x-forwarded-host"`). Removing
   the whole envoy_on_request block restores a parseable scalar.

2. Add `headers_to_add` natively under `authorization_request` of the
   ext_authz filter. This is where the auth headers belong: native
   Envoy substitutions don't have YAML escaping pitfalls, and they
   work whether or not the base Lua template defines envoy_on_request.

Headers added:
  - X-Original-URL  — the load-bearing one. Authelia /api/verify
                      reads this (NOT X-Forwarded-Uri) when computing
                      the post-login `rd`.
  - X-Forwarded-Method, X-Forwarded-Proto, X-Forwarded-Host,
    X-Forwarded-Uri — kept for cookie-scope / method checks and for
    forward compat with /api/authz/forward-auth.

Usage:
    kubectl -n media-stack cp tools/hotfix_envoy_ext_authz_recover.py \\
        deploy/media-stack-controller:/tmp/recover.py
    kubectl -n media-stack exec deploy/media-stack-controller -- \\
        python3 /tmp/recover.py
    kubectl -n media-stack rollout restart deploy/envoy
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ENVOY_YAML = Path("/srv-config/envoy/envoy.yaml")

# Match the entire envoy_on_request block as it appears INSIDE the
# YAML scalar (so all newlines are literal `\n` two-char sequences).
# Non-greedy up to the first `\nend\n` after the function header.
ENVOY_ON_REQUEST_PATTERN = re.compile(
    r"\\n\\nfunction envoy_on_request\(handle\)"
    r"\\n  -- \[AUTH\] Set forwarded headers for ext_authz"
    r".*?"
    r"\\nend",
    re.DOTALL,
)

# Anchor for inserting headers_to_add: the line that opens
# `authorization_response:` at column 16 (the standard indent for an
# ext_authz HTTP service block emitted by build_ext_authz_filter).
# Both compose and k8s configs use this exact indentation, so the
# anchor is reliable across deploys.
AUTHZ_RESPONSE_ANCHOR = "                authorization_response:\n"

# envoy.extensions.filters.http.ext_authz.v3.AuthorizationRequest
# .headers_to_add takes `repeated config.core.v3.HeaderValue` — i.e.
# {key, value} entries directly, NOT the {header: {key, value},
# append_action: ...} HeaderValueOption shape used by route-level
# request_headers_to_add. Mixing them up yields:
#   "no such field: 'header' has unknown fields" at config load.
HEADERS_TO_ADD_BLOCK = (
    "                  headers_to_add:\n"
    "                  - key: X-Original-URL\n"
    "                    value: '%REQ(X-FORWARDED-PROTO)%://%REQ(:AUTHORITY)%%REQ(:PATH)%'\n"
    "                  - key: X-Forwarded-Method\n"
    "                    value: '%REQ(:METHOD)%'\n"
    "                  - key: X-Forwarded-Proto\n"
    "                    value: '%REQ(X-FORWARDED-PROTO)%'\n"
    "                  - key: X-Forwarded-Host\n"
    "                    value: '%REQ(:AUTHORITY)%'\n"
    "                  - key: X-Forwarded-Uri\n"
    "                    value: '%REQ(:PATH)%'\n"
)

# Old (wrong) block from the previous recovery attempt — strip if
# present so re-running this script over a half-applied state ends
# up with the correct shape.
WRONG_HEADERS_TO_ADD_PATTERN = re.compile(
    r"                  headers_to_add:\n"
    r"(?:                  - header:\n                      key: [^\n]+\n                      value: [^\n]+\n)+",
)


def main() -> int:
    if not ENVOY_YAML.is_file():
        print(f"[recover] not found: {ENVOY_YAML}", file=sys.stderr)
        return 1
    text = ENVOY_YAML.read_text(encoding="utf-8")
    original_size = len(text)

    # Step 1: strip the broken envoy_on_request block.
    new_text, n = ENVOY_ON_REQUEST_PATTERN.subn("", text, count=1)
    if n:
        print(f"[recover] stripped envoy_on_request block ({original_size - len(new_text)} chars)")
    else:
        print("[recover] envoy_on_request block not found (already removed?)")

    # Step 2a: strip any wrong-shape headers_to_add from a prior attempt.
    new_text, stripped = WRONG_HEADERS_TO_ADD_PATTERN.subn("", new_text)
    if stripped:
        print(f"[recover] stripped {stripped} prior wrong-shape headers_to_add block(s)")

    # Step 2b: insert correctly-shaped headers_to_add IF not already
    # present in the right shape. We detect "right shape" by looking
    # for `key: X-Original-URL` at the headers_to_add indent level.
    correct_marker = "                  - key: X-Original-URL\n"
    if correct_marker in new_text:
        print("[recover] X-Original-URL already present in correct shape — skipping insert")
    elif AUTHZ_RESPONSE_ANCHOR not in new_text:
        print("[recover] could not find authorization_response anchor — bailing",
              file=sys.stderr)
        return 2
    else:
        # Insert headers_to_add JUST BEFORE authorization_response:
        new_text = new_text.replace(
            AUTHZ_RESPONSE_ANCHOR,
            HEADERS_TO_ADD_BLOCK + AUTHZ_RESPONSE_ANCHOR,
            1,
        )
        print("[recover] inserted headers_to_add (5 X-* headers) under authorization_request")

    if new_text == text:
        print("[recover] no changes needed")
        return 0

    backup = ENVOY_YAML.with_suffix(".yaml.recover-backup")
    shutil.copy2(ENVOY_YAML, backup)
    ENVOY_YAML.write_text(new_text, encoding="utf-8")
    print(f"[recover] backup: {backup}")
    print("[recover] now: kubectl -n media-stack rollout restart deploy/envoy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
