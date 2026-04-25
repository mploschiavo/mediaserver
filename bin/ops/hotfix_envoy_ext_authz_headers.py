"""Hot-patch the live envoy.yaml on the controller's PVC to add the
X-Forwarded-* headers the ext_authz filter needs.

Run inside the controller pod (which has python3 + the envoy PVC
mounted at /srv-config/envoy). Idempotent — re-running on an
already-patched file is a no-op.

Usage:
    kubectl -n media-stack cp bin/ops/hotfix_envoy_ext_authz_headers.py \\
        deploy/media-stack-controller:/tmp/hotfix.py
    kubectl -n media-stack exec deploy/media-stack-controller -- \\
        python3 /tmp/hotfix.py
    kubectl -n media-stack rollout restart deploy/envoy
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import yaml


ENVOY_YAML = Path("/srv-config/envoy/envoy.yaml")
EXT_AUTHZ_FILTER_NAME = "envoy.filters.http.ext_authz"
HEADERS_TO_ADD = [
    {"header": {"key": "X-Forwarded-Method",
                "value": "%REQ(:METHOD)%"}},
    {"header": {"key": "X-Forwarded-Proto",
                "value": "%REQ(X-FORWARDED-PROTO)%"}},
    {"header": {"key": "X-Forwarded-Host",
                "value": "%REQ(:AUTHORITY)%"}},
    {"header": {"key": "X-Forwarded-Uri",
                "value": "%REQ(:PATH)%"}},
]


def patch_filter(ext_authz_filter: dict) -> bool:
    """Add headers_to_add to the ext_authz filter in-place. Returns
    True if any change was made."""
    http_service = (ext_authz_filter.get("typed_config", {})
                    .get("http_service") or {})
    auth_req = http_service.setdefault("authorization_request", {})
    existing = auth_req.get("headers_to_add") or []
    existing_keys = {
        (h.get("header") or {}).get("key", "").lower()
        for h in existing if isinstance(h, dict)
    }
    added = False
    for entry in HEADERS_TO_ADD:
        key = entry["header"]["key"].lower()
        if key in existing_keys:
            continue
        existing.append(entry)
        added = True
    if added:
        auth_req["headers_to_add"] = existing
    return added


def main() -> int:
    if not ENVOY_YAML.is_file():
        print(f"[hotfix] not found: {ENVOY_YAML}", file=sys.stderr)
        return 1
    text = ENVOY_YAML.read_text(encoding="utf-8")
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print(f"[hotfix] parse failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(cfg, dict):
        print("[hotfix] envoy.yaml is not a mapping at root", file=sys.stderr)
        return 1

    listeners = (cfg.get("static_resources") or {}).get("listeners") or []
    patched_filters = 0
    for listener in listeners:
        for fc in listener.get("filter_chains") or []:
            for net_filter in fc.get("filters") or []:
                hcm = net_filter.get("typed_config") or {}
                for hf in hcm.get("http_filters") or []:
                    if hf.get("name") != EXT_AUTHZ_FILTER_NAME:
                        continue
                    if patch_filter(hf):
                        patched_filters += 1

    if not patched_filters:
        print("[hotfix] no changes needed (already patched or no ext_authz filter)")
        return 0

    backup = ENVOY_YAML.with_suffix(".yaml.prehotfix")
    shutil.copy2(ENVOY_YAML, backup)
    ENVOY_YAML.write_text(
        yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False,
                       allow_unicode=True),
        encoding="utf-8",
    )
    print(f"[hotfix] patched {patched_filters} ext_authz filter(s)")
    print(f"[hotfix] backup: {backup}")
    print("[hotfix] now: kubectl -n media-stack rollout restart deploy/envoy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
