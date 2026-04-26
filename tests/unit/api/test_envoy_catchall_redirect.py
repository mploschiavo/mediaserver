"""Ratchet: the gateway vhost MUST end with catch-all fallbacks
so users who type an unknown URL (``/bogus``, ``/random-path``,
``/app/unknown-service``) land on a useful page instead of the
bare Envoy 404.

Expected tail of the main gateway vhost's route list:

1.  ``prefix: /app/`` + Accept: text/html  →  ``path_redirect``
    under ``/app/`` (the dashboard or default app). Handles
    browsers typing an unknown service slug.
2.  ``prefix: /``     + Accept: text/html  →  ``path_redirect``
    under ``/app/`` (the default app). Handles browsers typing a
    path that isn't under ``/app/`` at all.
3.  ``prefix: /``     (no header match)    →  route to default
    cluster. Handles non-HTML clients (curl, TV apps) that skip
    the Accept header — they still proxy through to the default
    service instead of 404-ing.

If any of these disappear the stack regresses to "404 page" for
typos, which is bad UX. If the tail order changes, update the
assertion below only after confirming the new order still routes
every unmatched request somewhere useful.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - handled at runtime
    yaml = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

_ENVOY_YAML = ROOT / "dist" / "config" / "envoy" / "envoy.yaml"


def _html_accept_match(match: dict[str, Any]) -> bool:
    for h in match.get("headers") or []:
        if h.get("name") != "accept":
            continue
        regex = (h.get("safe_regex_match") or {}).get("regex", "")
        if "text/html" in regex:
            return True
    return False


def _load_gateway_vhost() -> dict[str, Any] | None:
    if yaml is None:
        return None
    if not _ENVOY_YAML.is_file():
        return None
    doc = yaml.safe_load(_ENVOY_YAML.read_text(encoding="utf-8"))
    for listener in (doc or {}).get("static_resources", {}).get("listeners", []):
        for fc in listener.get("filter_chains", []) or []:
            for f in fc.get("filters", []) or []:
                tc = (f or {}).get("typed_config") or {}
                rc = tc.get("route_config") or {}
                for vh in rc.get("virtual_hosts", []) or []:
                    domains = [str(d).lower() for d in (vh.get("domains") or [])]
                    if any("apps.media-stack.local" in d for d in domains):
                        return vh
    return None


class EnvoyCatchAllRatchet(unittest.TestCase):

    def setUp(self) -> None:
        if yaml is None:
            self.skipTest("PyYAML not installed")
        if not _ENVOY_YAML.is_file():
            self.skipTest("dist/config/envoy/envoy.yaml not generated yet")
        self.vhost = _load_gateway_vhost()
        if self.vhost is None:
            self.skipTest("gateway vhost not present in envoy.yaml")

    def test_tail_has_three_catchalls_in_order(self) -> None:
        routes = list(self.vhost.get("routes") or [])
        self.assertGreaterEqual(
            len(routes), 3,
            "Gateway vhost has fewer than 3 routes — catch-all tail can't be present.",
        )
        tail = routes[-3:]

        r1_match = tail[0].get("match") or {}
        r1_redirect = tail[0].get("redirect") or {}
        self.assertEqual(
            r1_match.get("prefix"), "/app/",
            f"Tail route 1 should be prefix /app/ (HTML catch-all). Got: {tail[0]!r}",
        )
        self.assertTrue(
            _html_accept_match(r1_match),
            f"Tail route 1 must require Accept: text/html. Got headers: {r1_match.get('headers')!r}",
        )
        self.assertTrue(
            str(r1_redirect.get("path_redirect", "")).startswith("/app/"),
            f"Tail route 1 must redirect under /app/. Got: {r1_redirect!r}",
        )

        r2_match = tail[1].get("match") or {}
        r2_redirect = tail[1].get("redirect") or {}
        self.assertEqual(
            r2_match.get("prefix"), "/",
            f"Tail route 2 should be prefix / (HTML catch-all). Got: {tail[1]!r}",
        )
        self.assertTrue(
            _html_accept_match(r2_match),
            f"Tail route 2 must require Accept: text/html. Got headers: {r2_match.get('headers')!r}",
        )
        self.assertTrue(
            str(r2_redirect.get("path_redirect", "")).startswith("/app/"),
            f"Tail route 2 must redirect under /app/. Got: {r2_redirect!r}",
        )

        r3_match = tail[2].get("match") or {}
        r3_route = tail[2].get("route") or {}
        self.assertEqual(
            r3_match.get("prefix"), "/",
            f"Tail route 3 should be prefix / (non-HTML proxy fallback). Got: {tail[2]!r}",
        )
        self.assertFalse(
            _html_accept_match(r3_match),
            "Tail route 3 must NOT gate on Accept: text/html — it's the non-HTML fallback "
            f"that catches curl/TV-app traffic. Got headers: {r3_match.get('headers')!r}",
        )
        self.assertTrue(
            str(r3_route.get("cluster", "")).startswith("service_"),
            f"Tail route 3 must proxy to a service cluster. Got: {r3_route!r}",
        )


if __name__ == "__main__":
    unittest.main()
