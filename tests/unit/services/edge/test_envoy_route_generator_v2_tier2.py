"""Tier-2 generator tests (PR-8) — rate-limit + geo ACL emission."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    HostEntry,
    HostGeoAcl,
    HostRateLimit,
    RoutingConfigV2,
)
from media_stack.services.edge.envoy_route_generator_v2 import (  # noqa: E402
    generate_route_config_v2,
)


def _vhost_for(rc: dict, domain: str) -> dict:
    for vh in rc["virtual_hosts"]:
        if domain in vh["domains"]:
            return vh
    raise AssertionError(f"no vhost for {domain}")


class TestRateLimit(unittest.TestCase):
    def test_per_second_emits_token_bucket(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    rate_limit=HostRateLimit(per_second=100, burst=200)))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        route = vh["routes"][0]
        self.assertIn("rate_limits", route)
        rl = route["typed_per_filter_config"]["envoy.filters.http.local_ratelimit"]
        bucket = rl["token_bucket"]
        self.assertEqual(bucket["tokens_per_fill"], 100)
        self.assertEqual(bucket["max_tokens"], 200)
        self.assertEqual(bucket["fill_interval"], "1s")

    def test_burst_defaults_to_per_second_when_zero(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    rate_limit=HostRateLimit(per_second=50)))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        rl = vh["routes"][0]["typed_per_filter_config"][
            "envoy.filters.http.local_ratelimit"
        ]
        # burst=0 → bucket size falls back to per_second so the bucket
        # isn't degenerate-tiny.
        self.assertEqual(rl["token_bucket"]["max_tokens"], 50)

    def test_zero_per_second_emits_no_rate_limit(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    rate_limit=HostRateLimit(per_second=0)))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertNotIn("rate_limits", vh["routes"][0])

    def test_no_rate_limit_when_field_absent(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertNotIn("rate_limits", vh["routes"][0])


class TestGeoAcl(unittest.TestCase):
    def test_allow_list_lands_in_metadata(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    geo_acl=HostGeoAcl(allow=["US", "CA"])))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        meta = vh["routes"][0]["metadata"]["filter_metadata"]["edge.geo_acl"]
        self.assertEqual(meta["allow"], ["US", "CA"])
        self.assertEqual(meta["deny"], [])

    def test_deny_list_lands_in_metadata(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    geo_acl=HostGeoAcl(deny=["RU", "KP"])))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        meta = vh["routes"][0]["metadata"]["filter_metadata"]["edge.geo_acl"]
        self.assertEqual(meta["deny"], ["RU", "KP"])

    def test_empty_lists_emit_no_metadata(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y",
                                    geo_acl=HostGeoAcl()))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertNotIn("metadata", vh["routes"][0])

    def test_no_geo_acl_when_field_absent(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(role="r", service_id="s", canonical="x.y"))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        self.assertNotIn("metadata", vh["routes"][0])


class TestRateLimitAndGeoAclCoexist(unittest.TestCase):
    def test_both_emitted_on_same_route(self) -> None:
        cfg = RoutingConfigV2(gateway_host="m.iomio.io")
        cfg.hosts.append(HostEntry(
            role="r", service_id="s", canonical="x.y",
            rate_limit=HostRateLimit(per_second=10),
            geo_acl=HostGeoAcl(allow=["US"]),
        ))
        rc = generate_route_config_v2(cfg)
        vh = _vhost_for(rc, "x.y")
        route = vh["routes"][0]
        self.assertIn("rate_limits", route)
        self.assertIn("metadata", route)


if __name__ == "__main__":
    unittest.main()
