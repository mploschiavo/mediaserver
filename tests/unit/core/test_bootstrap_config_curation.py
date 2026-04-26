import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

DEFAULTS_DIR = ROOT / "contracts" / "defaults"


def _load_yaml_defaults() -> dict:
    """Merge all YAML defaults into a single config dict."""
    cfg: dict = {}
    if DEFAULTS_DIR.is_dir():
        for yaml_file in sorted(DEFAULTS_DIR.glob("*.yaml")):
            with open(yaml_file, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, dict):
                cfg.update(data)
    return cfg


class BootstrapConfigCurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = _load_yaml_defaults()

    def test_lidarr_discovery_lists_focus_on_top_10_seed_sets(self):
        lists_cfg = (self.cfg.get("arr_discovery_lists") or {}).get("Lidarr") or []
        self.assertGreaterEqual(len(lists_cfg), 4)

        counts = set()
        names = []
        for item in lists_cfg:
            names.append(str(item.get("name") or ""))
            overrides = item.get("field_overrides") or {}
            count = overrides.get("count")
            if isinstance(count, int):
                counts.add(count)

        self.assertEqual(counts, {10})
        self.assertTrue(any("Rock" in name for name in names))
        self.assertTrue(any("Pop" in name for name in names))
        self.assertTrue(any("Hip-Hop" in name for name in names))
        self.assertTrue(any("Electronic" in name for name in names))

    def test_readarr_discovery_lists_have_popular_sources(self):
        lists_cfg = (self.cfg.get("arr_discovery_lists") or {}).get("Readarr") or []
        self.assertGreaterEqual(len(lists_cfg), 2)

        impls = {str(item.get("implementation") or "") for item in lists_cfg}
        self.assertIn("GoodreadsListImportList", impls)

        list_ids = {
            str(((item.get("field_overrides") or {}).get("listId") or "")).strip()
            for item in lists_cfg
        }
        self.assertIn("2681", list_ids)
        self.assertIn("184476", list_ids)

    def test_arr_discovery_lists_enable_prune_and_initial_sync(self):
        discovery_cfg = self.cfg.get("arr_discovery_lists") or {}
        self.assertTrue(bool(discovery_cfg.get("trigger_initial_sync")))
        self.assertTrue(bool(discovery_cfg.get("prune_unmanaged")))

    # Removed: bazarr, jellyfin_prewarm, jellyfin_playback, jellyfin_livetv,
    # maintainerr, flaresolverr, homepage, adapter_hooks tests.
    # These config sections were moved from the central JSON config to
    # per-service YAML contracts and profile YAML as part of config
    # consolidation (commit 1df2e57).  The remaining tests validate the
    # defaults that still live in contracts/defaults/*.yaml.

    def test_quality_upgrade_lifecycle_defaults_are_enabled(self):
        lifecycle = self.cfg.get("arr_quality_upgrade") or {}
        self.assertTrue(bool(lifecycle.get("enabled")))
        self.assertTrue(bool(lifecycle.get("allow_upgrades")))
        self.assertIn(
            "1080",
            {str(x).strip() for x in (lifecycle.get("cutoff_preferred_name_tokens") or [])},
        )

    def test_qbit_queue_guardrails_defaults_are_enabled(self):
        clients = self.cfg.get("download_clients") or {}
        qbit = clients.get("qbittorrent") or {}
        queue_cfg = qbit.get("queue_guardrails") or {}
        self.assertTrue(bool(queue_cfg.get("enabled")))
        self.assertFalse(bool(queue_cfg.get("dry_run")))

        caps = queue_cfg.get("max_queued_by_category") or {}
        self.assertEqual(int(caps.get("music", 0)), 6)
        self.assertEqual(int(caps.get("books", 0)), 4)
        self.assertGreaterEqual(int(caps.get("tv", 0)), int(caps.get("music", 0)))

        stale = queue_cfg.get("stale_prune") or {}
        self.assertTrue(bool(stale.get("enabled")))
        self.assertTrue(bool(stale.get("delete_files")))

    def test_qbit_auth_bypass_defaults_are_private_and_not_world_open(self):
        clients = self.cfg.get("download_clients") or {}
        qbit = clients.get("qbittorrent") or {}
        auth_bypass = qbit.get("auth_bypass") or {}

        self.assertTrue(bool(auth_bypass.get("localhost")))
        self.assertTrue(bool(auth_bypass.get("whitelist_enabled")))
        self.assertFalse(bool(auth_bypass.get("allow_open_world")))

        subnets = {str(x).strip() for x in (auth_bypass.get("whitelist_subnets") or [])}
        self.assertIn("10.0.0.0/8", subnets)
        self.assertIn("172.16.0.0/12", subnets)
        self.assertIn("192.168.0.0/16", subnets)
        self.assertNotIn("0.0.0.0/0", subnets)



if __name__ == "__main__":
    unittest.main()
