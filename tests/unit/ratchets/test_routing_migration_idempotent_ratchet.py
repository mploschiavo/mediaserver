"""Ratchet R-3: routing v1 → v2 migration is idempotent + lossless.

Two structural guarantees:

1. ``migrate(migrate(x).to_dict()) == migrate(x)`` for every v1
   sample. Running the migrator twice is the same as running it once.
2. Every meaningful v1 field lands somewhere visible in the v2 shape:
   no silent data loss. The migration table is documented in
   ``migrator.py``; this ratchet pins it.

Why a ratchet (not just a unit test):

* The set of v1 keys is finite and frozen (v1 is dead — no new fields
  arrive). The set of *places they land in v2* must remain stable
  through every refactor. A drive-by edit to ``migrate_v1_to_v2``
  that, say, drops ``app_path_prefix`` would silently corrupt every
  operator's existing config. The ratchet catches it.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.config.routing import migrate_v1_to_v2  # noqa: E402


# Every v1 key the controller has ever read (cross-reference
# `_routing.py:get_routing()` which returns the legacy view).
V1_KEYS_TO_PRESERVE = {
    "base_domain", "stack_subdomain", "gateway_host", "gateway_port",
    "app_path_prefix", "strategy", "scheme",
    # Booleans that became sub-objects:
    "internet_exposed",
    # Dict that became a list:
    "direct_hosts",
}


class RoutingMigrationIdempotentRatchet(unittest.TestCase):
    def test_migration_is_idempotent(self) -> None:
        v1 = {
            "base_domain": "iomio.io",
            "stack_subdomain": "m",
            "gateway_host": "m.iomio.io",
            "gateway_port": 443,
            "app_path_prefix": "/app",
            "strategy": "hybrid",
            "scheme": "https",
            "internet_exposed": True,
            "direct_hosts": {
                "media_server": "jf.iomio.io",
                "auth": "auth.iomio.io",
                "sonarr": "s.iomio.io",
            },
        }
        once = migrate_v1_to_v2(v1, media_server_id="jellyfin")
        twice = migrate_v1_to_v2(once.to_dict(), media_server_id="jellyfin")
        self.assertEqual(once, twice,
                         "Re-running the migrator on a v2 dict produced "
                         "a different shape — migration must be idempotent.")

    def test_top_level_v1_fields_preserved(self) -> None:
        v1 = {
            "base_domain": "iomio.io",
            "stack_subdomain": "ms",
            "gateway_host": "host.iomio.io",
            "gateway_port": 443,
            "app_path_prefix": "/myapp",
            "strategy": "subdomain",
            "scheme": "https",
        }
        cfg = migrate_v1_to_v2(v1)
        self.assertEqual(cfg.base_domain, "iomio.io")
        self.assertEqual(cfg.stack_subdomain, "ms")
        self.assertEqual(cfg.gateway_host, "host.iomio.io")
        self.assertEqual(cfg.gateway_port, 443)
        self.assertEqual(cfg.app_path_prefix, "/myapp")
        self.assertEqual(cfg.strategy.value, "subdomain")
        self.assertEqual(cfg.scheme, "https")

    def test_internet_exposed_lands_in_exposure_enabled(self) -> None:
        cfg_on = migrate_v1_to_v2({"internet_exposed": True})
        cfg_off = migrate_v1_to_v2({"internet_exposed": False})
        self.assertTrue(cfg_on.exposure.enabled)
        self.assertFalse(cfg_off.exposure.enabled)

    def test_direct_hosts_become_host_entries(self) -> None:
        cfg = migrate_v1_to_v2({
            "direct_hosts": {"sonarr": "s.example", "radarr": "r.example"},
        })
        roles = {h.role for h in cfg.hosts}
        self.assertEqual(roles, {"sonarr", "radarr"})
        canonical = {h.canonical for h in cfg.hosts}
        self.assertEqual(canonical, {"s.example", "r.example"})

    def test_v1_keys_referenced_in_migrator_source(self) -> None:
        # Belt-and-suspenders: the migrator source must mention every
        # v1 key — if a future refactor drops one, this fails.
        migrator = (
            ROOT / "src" / "media_stack" / "api" / "services" / "config"
            / "routing" / "migrator.py"
        ).read_text(encoding="utf-8")
        missing = sorted(k for k in V1_KEYS_TO_PRESERVE if k not in migrator)
        self.assertEqual(
            missing, [],
            f"migrator.py no longer mentions v1 keys: {missing}. "
            f"Either preserve the field handling or update the "
            f"V1_KEYS_TO_PRESERVE allowlist if the field is genuinely "
            f"unused.",
        )


if __name__ == "__main__":
    unittest.main()
