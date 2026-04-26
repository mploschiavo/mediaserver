"""Tests for the v1 → v2 routing config migrator.

The migrator's contract:

* v1 dicts (legacy ``direct_hosts`` flat shape) get a structured v2
  shape preserving every meaningful field.
* v2 dicts pass through ``RoutingConfigV2.from_dict`` unchanged
  (idempotent — running migration twice is the same as once).
* Empty / None inputs yield a default v2 config (no crashes).
* The legacy ``direct_hosts.media_server`` role is resolved through
  the profile's media-server selection (the controller calls this with
  ``media_server_id="jellyfin"``); other roles map 1:1 unless they're
  semantic (``auth`` → ``authelia``).
* The auth-provider host (``service_id == "authelia"``) NEVER gets a
  required auth gate — gating it with itself locks operators out.

These tests exercise each branch and the integration with the
schema. No I/O — pure functions.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing.schema_v2 import (  # noqa: E402
    AuthGate,
    Binding,
    HostEntry,
    Strategy,
)
from media_stack.api.services.config.routing.migrator import (  # noqa: E402
    migrate_v1_to_v2,
)


class TestVersionDetection(unittest.TestCase):
    """The migrator must distinguish v1 from v2 inputs by either an
    explicit ``version`` field OR the presence of a v2-only key."""

    def test_explicit_version_2_passes_through(self) -> None:
        cfg = migrate_v1_to_v2({"version": 2, "base_domain": "x"})
        self.assertEqual(cfg.version, 2)
        self.assertEqual(cfg.base_domain, "x")

    def test_v2_only_key_marks_as_v2(self) -> None:
        # Just having ``hosts`` (a v2-only key) skips migration.
        cfg = migrate_v1_to_v2({
            "hosts": [{"role": "r", "service_id": "s", "canonical": "c.x"}],
        })
        self.assertEqual(len(cfg.hosts), 1)
        self.assertEqual(cfg.hosts[0].canonical, "c.x")

    def test_no_version_no_v2_keys_is_v1(self) -> None:
        cfg = migrate_v1_to_v2({"direct_hosts": {"sonarr": "s.example"}})
        self.assertEqual(cfg.version, 2)
        self.assertEqual(len(cfg.hosts), 1)


class TestDirectHostsMigration(unittest.TestCase):
    """v1 ``direct_hosts: {role: hostname}`` becomes ``hosts: [...]``"""

    def test_media_server_resolves_via_profile(self) -> None:
        cfg = migrate_v1_to_v2(
            {"direct_hosts": {"media_server": "jf.iomio.io"}},
            media_server_id="plex",
        )
        h = cfg.hosts[0]
        self.assertEqual(h.role, "media_server")
        self.assertEqual(h.service_id, "plex")
        self.assertEqual(h.canonical, "jf.iomio.io")

    def test_media_server_falls_back_to_jellyfin_when_profile_unset(self) -> None:
        cfg = migrate_v1_to_v2(
            {"direct_hosts": {"media_server": "media.example"}},
            media_server_id=None,
        )
        self.assertEqual(cfg.hosts[0].service_id, "jellyfin")

    def test_auth_role_resolves_to_authelia(self) -> None:
        cfg = migrate_v1_to_v2({"direct_hosts": {"auth": "auth.example"}})
        h = cfg.hosts[0]
        self.assertEqual(h.role, "auth")
        self.assertEqual(h.service_id, "authelia")

    def test_unknown_role_treated_as_literal_service_id(self) -> None:
        # v1 used arbitrary keys as service ids when not semantic.
        cfg = migrate_v1_to_v2({"direct_hosts": {"sonarr": "s.example"}})
        self.assertEqual(cfg.hosts[0].service_id, "sonarr")

    def test_auth_host_never_gets_required_auth_gate(self) -> None:
        # Auth provider can't gate itself — operators get locked out.
        cfg = migrate_v1_to_v2(
            {"direct_hosts": {"auth": "auth.example", "sonarr": "s.example"}},
            auth_gate_default=AuthGate.REQUIRED,
        )
        auth_host = next(h for h in cfg.hosts if h.role == "auth")
        sonarr_host = next(h for h in cfg.hosts if h.role == "sonarr")
        self.assertEqual(auth_host.auth.gate, AuthGate.NONE)
        self.assertEqual(sonarr_host.auth.gate, AuthGate.REQUIRED)

    def test_auth_gate_default_none_propagates(self) -> None:
        cfg = migrate_v1_to_v2(
            {"direct_hosts": {"sonarr": "s.example"}},
            auth_gate_default=AuthGate.NONE,
        )
        self.assertEqual(cfg.hosts[0].auth.gate, AuthGate.NONE)

    def test_empty_hostnames_dropped(self) -> None:
        # v1 used empty strings to "unset" a role.
        cfg = migrate_v1_to_v2({
            "direct_hosts": {
                "sonarr": "s.example",
                "radarr": "",
                "lidarr": None,
            },
        })
        roles = [h.role for h in cfg.hosts]
        self.assertEqual(roles, ["sonarr"])

    def test_hosts_sorted_by_role_for_stable_diffs(self) -> None:
        cfg = migrate_v1_to_v2({"direct_hosts": {
            "z_service": "z.example",
            "a_service": "a.example",
            "m_service": "m.example",
        }})
        self.assertEqual(
            [h.role for h in cfg.hosts],
            ["a_service", "m_service", "z_service"],
        )


class TestExposureMigration(unittest.TestCase):
    def test_internet_exposed_carries_to_exposure_enabled(self) -> None:
        cfg = migrate_v1_to_v2({
            "internet_exposed": True,
            "gateway_host": "m.iomio.io",
            "direct_hosts": {"sonarr": "s.iomio.io"},
        })
        self.assertTrue(cfg.exposure.enabled)
        # gateway_host + every host's canonical land in public_hostnames.
        self.assertIn("m.iomio.io", cfg.exposure.public_hostnames)
        self.assertIn("s.iomio.io", cfg.exposure.public_hostnames)

    def test_default_internet_exposed_false_yields_empty_public_hostnames(self) -> None:
        cfg = migrate_v1_to_v2({"gateway_host": "x.example"})
        self.assertFalse(cfg.exposure.enabled)
        self.assertEqual(cfg.exposure.public_hostnames, [])

    def test_binding_defaults_to_auto(self) -> None:
        cfg = migrate_v1_to_v2({"internet_exposed": True})
        self.assertEqual(cfg.exposure.binding, Binding.AUTO)


class TestTopLevelFieldsCarry(unittest.TestCase):
    def test_all_v1_top_level_fields_preserved(self) -> None:
        v1 = {
            "base_domain": "iomio.io",
            "stack_subdomain": "m",
            "gateway_host": "m.iomio.io",
            "gateway_port": 443,
            "strategy": "subdomain",
            "scheme": "https",
            "app_path_prefix": "/app",
        }
        cfg = migrate_v1_to_v2(v1)
        self.assertEqual(cfg.base_domain, "iomio.io")
        self.assertEqual(cfg.stack_subdomain, "m")
        self.assertEqual(cfg.gateway_host, "m.iomio.io")
        self.assertEqual(cfg.gateway_port, 443)
        self.assertEqual(cfg.strategy, Strategy.SUBDOMAIN)
        self.assertEqual(cfg.scheme, "https")
        self.assertEqual(cfg.app_path_prefix, "/app")


class TestEdgeCases(unittest.TestCase):
    def test_none_input_yields_default(self) -> None:
        cfg = migrate_v1_to_v2(None)
        self.assertEqual(cfg.version, 2)
        self.assertEqual(cfg.hosts, [])

    def test_empty_dict_yields_default(self) -> None:
        cfg = migrate_v1_to_v2({})
        self.assertEqual(cfg.version, 2)
        self.assertEqual(cfg.hosts, [])

    def test_idempotent_v2_passthrough(self) -> None:
        # Running migration twice on a v2 input is the same as once.
        v2 = migrate_v1_to_v2({
            "internet_exposed": True,
            "gateway_host": "x.example",
            "direct_hosts": {"sonarr": "s.example"},
        })
        v2_dict = v2.to_dict()
        v2_again = migrate_v1_to_v2(v2_dict)
        self.assertEqual(v2_again.to_dict(), v2_dict)

    def test_garbage_direct_hosts_doesnt_crash(self) -> None:
        cfg = migrate_v1_to_v2({"direct_hosts": "not a dict"})
        self.assertEqual(cfg.hosts, [])


if __name__ == "__main__":
    unittest.main()
