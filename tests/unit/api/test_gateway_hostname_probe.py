"""Regression test: Gateway hostnames panel must not leak Lua / proto / JS tokens.

A previous revision of ``_GatewayHostnameProbe`` regex-scanned the
rendered ``envoy.yaml`` as a "secondary" hostname source. envoy.yaml
embeds inline Lua scripts (with ``string.find``, ``string.gsub`` etc.),
Envoy proto type URLs (``envoy.extensions.filters.http.ext_authz.v3``,
``type.googleapis.com``), and minified-JS variable accesses
(``a.get``, ``el.parent``, ``u.hash``). The regex matched all of them
and the operator's Routing tab showed the garbage alongside the real
hosts. Operator quote: "some of these look like javascript dom objects".

The fix is to trust routing config (the source of truth that drives the
envoy.yaml render in the first place). This test guarantees the leak
can never come back.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api import handlers_get  # noqa: E402


class GatewayHostnameProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.probe = handlers_get._GatewayHostnameProbe()

    def _patch_routing(self, routing: dict[str, Any], services: list[Any]) -> Any:
        # Patch the underlying functions/attrs the probe imports lazily.
        # Use ``contextlib.ExitStack`` so multiple patches can compose.
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch(
            "media_stack.api.services.config.get_routing",
            return_value=routing,
        ))
        stack.enter_context(mock.patch(
            "media_stack.api.services.registry.SERVICES",
            services,
        ))
        return stack

    def test_pure_routing_config_no_envoy_scrape(self) -> None:
        """The probe must read ONLY from routing config — gateway_host,
        per-service convention, and direct_hosts overrides. No regex
        over envoy.yaml."""
        services = [mock.Mock(id="jellyfin"), mock.Mock(id="sonarr")]
        routing = {
            "base_domain": "iomio.io",
            "stack_subdomain": "media-stack",
            "gateway_host": "m.iomio.io",
            "direct_hosts": {
                "media_server": "jf.iomio.io",
                "identity_provider": "auth.iomio.io",
            },
        }
        with self._patch_routing(routing, services):
            hosts = self.probe.read()
        self.assertEqual(
            sorted(hosts),
            sorted([
                "m.iomio.io",
                "jf.iomio.io",
                "auth.iomio.io",
                "jellyfin.media-stack.iomio.io",
                "sonarr.media-stack.iomio.io",
            ]),
        )

    def test_no_lua_or_proto_tokens_when_routing_empty(self) -> None:
        """Even with empty routing config the probe must NOT fall
        back to scraping envoy.yaml. Empty in → empty out."""
        with self._patch_routing({}, []):
            hosts = self.probe.read()
        self.assertEqual(hosts, [])

    def test_does_not_have_envoy_yaml_scraper_helpers(self) -> None:
        """Belt-and-suspenders: the dead helpers from the regex era
        (_HOST_RE, _locate_envoy_yaml, _looks_like_hostname) must
        stay removed. Their presence implies the leak path is back."""
        for attr in ("_HOST_RE", "_locate_envoy_yaml", "_looks_like_hostname"):
            self.assertFalse(
                hasattr(self.probe, attr) or hasattr(handlers_get._GatewayHostnameProbe, attr),
                f"{attr} re-introduced — risks reintroducing the Lua/proto/JS leak.",
            )


if __name__ == "__main__":
    unittest.main()
