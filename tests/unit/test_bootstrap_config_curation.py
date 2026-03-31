import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "bootstrap" / "media-stack.bootstrap.json"


class BootstrapConfigCurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

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

    def test_bazarr_integration_defaults_are_enabled(self):
        bazarr = self.cfg.get("bazarr") or {}
        self.assertTrue(bool(bazarr.get("enabled")))

        subtitle_defaults = bazarr.get("subtitle_defaults") or {}
        self.assertTrue(bool(subtitle_defaults.get("enabled")))

        providers = subtitle_defaults.get("providers") or []
        self.assertIn("opensubtitlescom", providers)
        self.assertIn("podnapisi", providers)

        general = subtitle_defaults.get("general") or {}
        self.assertTrue(bool(general.get("serie_default_enabled")))
        self.assertTrue(bool(general.get("movie_default_enabled")))

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

    def test_jellyfin_prewarm_and_media_hygiene_defaults_are_enabled(self):
        prewarm = self.cfg.get("jellyfin_prewarm") or {}
        self.assertTrue(bool(prewarm.get("enabled")))
        self.assertTrue(bool(prewarm.get("refresh_library")))
        self.assertTrue(bool(prewarm.get("refresh_guide")))

        hygiene = self.cfg.get("media_hygiene") or {}
        self.assertTrue(bool(hygiene.get("enabled")))
        self.assertTrue(bool(hygiene.get("cleanup_arr_failed_queue")))
        queue_cfg = hygiene.get("arr_failed_queue_cleanup") or {}
        self.assertTrue(bool(queue_cfg.get("blocklist")))
        fs = hygiene.get("filesystem") or {}
        self.assertTrue(bool(fs.get("enabled")))
        dedupe_cfg = fs.get("dedupe") or {}
        self.assertFalse(bool(dedupe_cfg.get("dry_run")))

        qbit_prune_cfg = hygiene.get("qbit_duplicate_prune") or {}
        self.assertIn("enabled", qbit_prune_cfg)
        self.assertFalse(bool(qbit_prune_cfg.get("enabled")))

        qbit_ipfilter_cfg = hygiene.get("qbit_ipfilter") or {}
        self.assertTrue(bool(qbit_ipfilter_cfg.get("enabled")))
        self.assertIn("DavidMoore/ipfilter", str(qbit_ipfilter_cfg.get("url", "")))
        self.assertEqual(int(qbit_ipfilter_cfg.get("min_refresh_interval_hours", 0)), 24)
        mirrors = {str(x).strip() for x in (qbit_ipfilter_cfg.get("mirror_target_paths") or [])}
        self.assertEqual(mirrors, set())

    def test_jellyfin_livetv_self_healing_defaults_are_enabled(self):
        live_tv = self.cfg.get("jellyfin_livetv") or {}
        self.assertTrue(bool(live_tv.get("enabled")))
        self.assertTrue(bool(live_tv.get("cleanup_duplicates")))
        self.assertTrue(bool(live_tv.get("recreate_managed_guides")))
        self.assertTrue(bool(live_tv.get("prune_unmanaged_tuners")))
        self.assertTrue(bool(live_tv.get("prune_unmanaged_guides")))
        self.assertTrue(bool(live_tv.get("fallback_enable_all_tuners_when_mapping_missing")))

        tuners = live_tv.get("tuners") or []
        self.assertGreaterEqual(len(tuners), 1)
        first = tuners[0] if isinstance(tuners[0], dict) else {}
        self.assertTrue(bool(first.get("normalize_tvg_id_suffix")))
        self.assertTrue(bool(first.get("filter_to_guide_channels")))
        self.assertIn("jellyfin/livetv-tuners", str(first.get("materialized_output_path", "")))

    def test_maintainerr_policy_defaults_are_declared(self):
        maintainerr = self.cfg.get("maintainerr") or {}
        self.assertTrue(bool(maintainerr.get("enabled")))
        integrations = maintainerr.get("integrations") or {}
        main_cfg = integrations.get("main") or {}
        self.assertTrue(bool(main_cfg.get("enabled")))
        self.assertEqual(str(main_cfg.get("media_server_type") or "").lower(), "jellyfin")
        self.assertIn("maintainerr.local", str(main_cfg.get("application_url") or ""))
        policy = maintainerr.get("policy") or {}
        retention = policy.get("retention") or {}
        self.assertEqual(int(retention.get("max_disk_used_percent", 0)), 65)
        self.assertGreater(int(retention.get("target_disk_used_percent", 0)), 0)
        rules_library = maintainerr.get("rules_library") or {}
        self.assertTrue(bool(rules_library.get("enabled")))
        self.assertTrue(bool(rules_library.get("include_defaults")))
        self.assertEqual(str(rules_library.get("merge_mode") or "").lower(), "append")
        self.assertIn("maintainerr/rules", str(rules_library.get("relative_path") or ""))

    def test_flaresolverr_proxy_defaults_are_declared(self):
        flaresolverr = self.cfg.get("flaresolverr") or {}
        self.assertTrue(bool(flaresolverr.get("enabled")))
        self.assertEqual(
            str(flaresolverr.get("url") or "").rstrip("/"),
            "http://flaresolverr:8191",
        )
        self.assertEqual(int(flaresolverr.get("request_timeout_seconds", 0)), 60)
        self.assertTrue(bool(flaresolverr.get("test_connection")))


if __name__ == "__main__":
    unittest.main()
