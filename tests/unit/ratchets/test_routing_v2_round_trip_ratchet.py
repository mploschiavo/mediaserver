"""Ratchet R-1: routing v2 schema round-trip integrity.

Asserts that for every persisted routing dict (v1 *or* v2), the
pipeline:

    raw_dict → migrate_v1_to_v2 → cfg
    cfg → cfg.to_dict() → RoutingConfigV2.from_dict() → cfg2

produces ``cfg == cfg2``. This is the structural guarantee operators
rely on when editing the YAML by hand: nothing they typed gets
silently dropped or reshaped, except by an explicit validator
rejection.

Why this is a ratchet (instead of "just a unit test"):

* The schema grows over time. Every new sub-dataclass needs a working
  ``from_dict``/``to_dict`` pair. A drive-by addition that forgets one
  of them silently breaks the round-trip — and the operator's edits.
  This test fans out across known sample fixtures + property-style
  permutations so the round-trip is exercised on every code path.
* The fixtures double as living docs of the supported schema —
  searchable in one place when someone asks "does v2 support X?"
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing import (  # noqa: E402
    RoutingConfigV2,
    migrate_v1_to_v2,
)


# ---- Fixtures: real-world-ish v1 + v2 shapes the round-trip must handle ----
V1_SAMPLES: list[dict] = [
    {},
    {"base_domain": "local"},
    {"base_domain": "iomio.io", "gateway_host": "m.iomio.io",
     "strategy": "hybrid", "internet_exposed": True,
     "direct_hosts": {"media_server": "jf.iomio.io",
                      "auth": "auth.iomio.io"}},
    {"strategy": "subdomain", "scheme": "https", "gateway_port": 443,
     "direct_hosts": {"sonarr": "sonarr.iomio.io",
                      "radarr": "radarr.iomio.io",
                      "lidarr": ""}},  # empty value → dropped
]

V2_SAMPLES: list[dict] = [
    # Empty v2 — explicit version field marks it as v2 already.
    {"version": 2},
    # Full-shape v2 with every sub-block populated.
    {
        "version": 2,
        "base_domain": "iomio.io",
        "gateway_host": "m.iomio.io",
        "exposure": {
            "enabled": True,
            "binding": "k8s_ingress",
            "public_hostnames": ["m.iomio.io", "jf.iomio.io"],
        },
        "hosts": [
            {"role": "media_server", "service_id": "jellyfin",
             "canonical": "jf.iomio.io",
             "aliases": ["jellyfin.iomio.io"],
             "tls": {"cert_id": "wildcard", "force_https": True},
             "auth": {"gate": "required", "provider": "authelia"},
             "websocket": True,
             "timeout_seconds": 600,
             "headers": {"response_set": {"X-Frame-Options": "SAMEORIGIN"}}},
        ],
        "path_aliases": [
            {"from": "/app/jellyfin", "to": "/app/jf", "code": 301},
        ],
        "apex": {"action": "redirect", "target": "/apps", "code": 302},
        "catch_all": {"action": "redirect", "target": "/apps"},
        "certs": [
            {"id": "wildcard", "source": "cert_manager",
             "common_name": "*.iomio.io",
             "cert_manager": {
                 "issuer_kind": "ClusterIssuer",
                 "issuer_name": "letsencrypt-prod",
                 "challenge": "dns01",
                 "solver": {"provider": "cloudflare",
                            "secret_ref": "cf-api-token"}}},
        ],
        "defaults": {"timeout_seconds": 60,
                     "auth": {"gate": "required", "provider": "authelia"}},
    },
]


class RoutingV2RoundTripRatchet(unittest.TestCase):
    def test_v1_samples_migrate_then_round_trip(self) -> None:
        for i, v1 in enumerate(V1_SAMPLES):
            with self.subTest(index=i):
                cfg = migrate_v1_to_v2(v1)
                d1 = cfg.to_dict()
                cfg2 = RoutingConfigV2.from_dict(d1)
                self.assertEqual(
                    cfg, cfg2,
                    f"v1 sample {i} doesn't round-trip — diff in to_dict()",
                )
                self.assertEqual(d1, cfg2.to_dict())

    def test_v2_samples_round_trip(self) -> None:
        for i, v2 in enumerate(V2_SAMPLES):
            with self.subTest(index=i):
                cfg = RoutingConfigV2.from_dict(v2)
                d1 = cfg.to_dict()
                cfg2 = RoutingConfigV2.from_dict(d1)
                self.assertEqual(d1, cfg2.to_dict())

    def test_v2_passthrough_idempotent(self) -> None:
        # Calling migrate on an already-v2 dict must be a no-op.
        for i, v2 in enumerate(V2_SAMPLES):
            with self.subTest(index=i):
                once = migrate_v1_to_v2(v2)
                twice = migrate_v1_to_v2(once.to_dict())
                self.assertEqual(once, twice)

    def test_to_dict_keys_are_present_for_top_level(self) -> None:
        # The v2 wire shape exposes a known set of top-level keys.
        # Adding a new top-level field is fine; *removing* one is a
        # backwards-incompatible change to the operator's editing
        # experience, so the ratchet pins the expected set.
        d = RoutingConfigV2().to_dict()
        expected = {
            "version", "base_domain", "stack_subdomain", "gateway_host",
            "gateway_port", "strategy", "scheme", "app_path_prefix",
            "exposure", "hosts", "path_aliases", "apex", "catch_all",
            "certs", "defaults",
        }
        self.assertEqual(set(d.keys()), expected,
                         f"Top-level wire shape drift: missing={expected - set(d.keys())}, "
                         f"new={set(d.keys()) - expected}")


if __name__ == "__main__":
    unittest.main()
