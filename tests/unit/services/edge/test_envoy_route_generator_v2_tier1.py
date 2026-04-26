"""Tier-1 generator tests (PR-6) — timeouts, headers, websocket,
maintenance flag.

Each Tier-1 knob has the same shape: per-host explicit value beats
the global default; default fills in when the host left the field
unset (zero / empty / None). These tests pin both layers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    AuthGate,
    HostAuth,
    HostEntry,
    HostHeaders,
    RoutingConfigV2,
    RoutingDefaults,
)
from media_stack.services.edge.envoy_route_generator_v2 import (  # noqa: E402
    generate_route_config_v2,
)


def _vhost_for(rc: dict, domain: str) -> dict:
    for vh in rc["virtual_hosts"]:
        if domain in vh["domains"]:
            return vh
    raise AssertionError(f"no vhost for {domain}")


class TestTimeouts(unittest.TestCase):
    def test_per_host_timeout_emitted(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="jellyfin",
                                    canonical="jf.iomio.io",
                                    timeout_seconds=600))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "jf.iomio.io")
        self.assertEqual(vh["routes"][0]["route"]["timeout"], "600s")

    def test_default_timeout_inherited(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io",
                               defaults=RoutingDefaults(timeout_seconds=60))
        cfg.hosts.append(HostEntry(role="r", service_id="jellyfin",
                                    canonical="jf.iomio.io"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "jf.iomio.io")
        self.assertEqual(vh["routes"][0]["route"]["timeout"], "60s")

    def test_host_overrides_default(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io",
                               defaults=RoutingDefaults(timeout_seconds=60))
        cfg.hosts.append(HostEntry(role="r", service_id="jellyfin",
                                    canonical="jf.iomio.io",
                                    timeout_seconds=600))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "jf.iomio.io")
        self.assertEqual(vh["routes"][0]["route"]["timeout"], "600s")

    def test_no_timeout_when_both_host_and_default_zero(self) -> None:
        # Need to override the defaults' default (60s) explicitly to
        # zero — otherwise the inherited 60s lands on the route. This
        # is the "I don't want any timeout pinned" path.
        cfg = RoutingConfigV2(gateway_host="m.iomio.io",
                               defaults=RoutingDefaults(timeout_seconds=0))
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertNotIn("timeout", vh["routes"][0]["route"])


class TestWebsockets(unittest.TestCase):
    def test_websocket_per_host_emits_upgrade_config(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="jellyfin",
                                    canonical="jf.iomio.io",
                                    websocket=True))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "jf.iomio.io")
        self.assertEqual(vh["routes"][0]["route"]["upgrade_configs"],
                          [{"upgrade_type": "websocket"}])

    def test_websocket_default_propagates(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io",
                               defaults=RoutingDefaults(websocket=True))
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertEqual(vh["routes"][0]["route"]["upgrade_configs"],
                          [{"upgrade_type": "websocket"}])

    def test_websocket_off_omits_upgrade_config(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertNotIn("upgrade_configs", vh["routes"][0]["route"])


class TestHeaders(unittest.TestCase):
    def test_per_host_response_set(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    headers=HostHeaders(response_set={
                                        "Strict-Transport-Security": "max-age=31536000",
                                    })))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        headers = vh["routes"][0].get("response_headers_to_add", [])
        self.assertEqual(len(headers), 1)
        self.assertEqual(headers[0]["header"]["key"], "Strict-Transport-Security")

    def test_response_remove_passes_through(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    headers=HostHeaders(response_remove=["Server"])))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertEqual(vh["routes"][0]["response_headers_to_remove"], ["Server"])

    def test_host_headers_merge_with_defaults(self) -> None:
        cfg = RoutingConfigV2(
            gateway_host="m.iomio.io",
            defaults=RoutingDefaults(
                headers=HostHeaders(response_set={"X-Frame-Options": "SAMEORIGIN"}),
            ),
        )
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    headers=HostHeaders(response_set={
                                        "Strict-Transport-Security": "max-age=1",
                                    })))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        keys = {h["header"]["key"] for h in vh["routes"][0]["response_headers_to_add"]}
        self.assertIn("X-Frame-Options", keys)
        self.assertIn("Strict-Transport-Security", keys)


class TestMaintenanceMode(unittest.TestCase):
    def test_maintenance_emits_503_direct_response(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="jellyfin",
                                    canonical="jf.iomio.io",
                                    maintenance=True))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "jf.iomio.io")
        route = vh["routes"][0]
        self.assertEqual(route["direct_response"]["status"], 503)
        self.assertNotIn("route", route)  # no forward when in maintenance

    def test_maintenance_message_mentions_service(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="jellyfin",
                                    canonical="jf.iomio.io",
                                    maintenance=True))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "jf.iomio.io")
        body = vh["routes"][0]["direct_response"]["body"]["inline_string"]
        self.assertIn("jellyfin", body)
        self.assertIn("maintenance", body.lower())


class TestHostAuthDoesntBreakRouting(unittest.TestCase):
    """Sanity: auth gate is a vhost-level concern (PR-6.5) — it
    shouldn't disturb the route action shape."""

    def test_required_auth_doesnt_change_route_action(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    auth=HostAuth(gate=AuthGate.REQUIRED,
                                                   provider="authelia")))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertEqual(vh["routes"][0]["route"]["cluster"], "service_s")


if __name__ == "__main__":
    unittest.main()
