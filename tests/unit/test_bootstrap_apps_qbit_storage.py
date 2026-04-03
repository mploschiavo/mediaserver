import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap_services.apps.servarr.runtime.qbit_ops as MODULE
import bootstrap_services.apps.servarr.runtime.factory as SERVARR_FACTORY
import bootstrap_services.apps.servarr.runtime.qbit_ops as QB_OPS


class QBittorrentStorageDefaultsTests(unittest.TestCase):
    def test_remove_on_limit_is_forced_to_pause_for_arr_compatibility(self):
        captured = {}

        def fake_set_preferences(opener, base_url, preferences):
            del opener, base_url
            captured["prefs"] = preferences

        qbit_cfg = {
            "default_save_path": "/data/torrents/completed",
            "temp_path": "/data/torrents/incomplete",
            "temp_path_enabled": True,
            "auto_tmm_enabled": True,
            "seeding_policy": {
                "enabled": True,
                "max_ratio": 1.2,
                "max_seeding_time_minutes": 1440,
                "remove_on_limit_reached": True,
            },
        }

        with mock.patch.object(QB_OPS, "qbit_set_preferences", side_effect=fake_set_preferences):
            with mock.patch.object(SERVARR_FACTORY, "log"):
                MODULE.setup_qbit_storage_defaults(
                    opener=object(),
                    qbit_url="http://qbittorrent:8080",
                    qbit_cfg=qbit_cfg,
                )

        prefs = captured["prefs"]
        self.assertTrue(prefs.get("max_ratio_enabled"))
        self.assertEqual(prefs.get("max_ratio"), 1.2)
        self.assertTrue(prefs.get("max_seeding_time_enabled"))
        self.assertEqual(prefs.get("max_seeding_time"), 1440)
        self.assertEqual(
            prefs.get("max_ratio_act"),
            0,
            "Arr compatibility requires pause-on-limit, not remove-on-limit.",
        )

    def test_auth_bypass_defaults_apply_private_ranges(self):
        captured = {}

        def fake_set_preferences(opener, base_url, preferences):
            del opener, base_url
            captured["prefs"] = preferences

        qbit_cfg = {
            "default_save_path": "/data/torrents/completed",
            "temp_path": "/data/torrents/incomplete",
            "temp_path_enabled": True,
            "auto_tmm_enabled": True,
        }

        with mock.patch.object(QB_OPS, "qbit_set_preferences", side_effect=fake_set_preferences):
            with mock.patch.object(SERVARR_FACTORY, "log"):
                MODULE.setup_qbit_storage_defaults(
                    opener=object(),
                    qbit_url="http://qbittorrent:8080",
                    qbit_cfg=qbit_cfg,
                )

        prefs = captured["prefs"]
        self.assertTrue(prefs.get("bypass_local_auth"))
        self.assertTrue(prefs.get("bypass_auth_subnet_whitelist_enabled"))
        self.assertFalse(prefs.get("web_ui_host_header_validation_enabled"))
        self.assertTrue(prefs.get("web_ui_reverse_proxy_enabled"))
        self.assertFalse(prefs.get("web_ui_csrf_protection_enabled"))
        whitelist = str(prefs.get("bypass_auth_subnet_whitelist") or "")
        self.assertIn("10.0.0.0/8", whitelist)
        self.assertIn("172.16.0.0/12", whitelist)
        self.assertIn("192.168.0.0/16", whitelist)

    def test_auth_bypass_filters_world_open_subnet_unless_explicitly_allowed(self):
        captured = {}
        logs = []

        def fake_set_preferences(opener, base_url, preferences):
            del opener, base_url
            captured["prefs"] = preferences

        qbit_cfg = {
            "default_save_path": "/data/torrents/completed",
            "temp_path": "/data/torrents/incomplete",
            "temp_path_enabled": True,
            "auto_tmm_enabled": True,
            "auth_bypass": {
                "localhost": True,
                "whitelist_enabled": True,
                "whitelist_subnets": ["10.0.0.0/8", "0.0.0.0/0"],
                "allow_open_world": False,
            },
        }

        with mock.patch.object(QB_OPS, "qbit_set_preferences", side_effect=fake_set_preferences):
            with mock.patch.object(
                SERVARR_FACTORY,
                "log",
                side_effect=lambda msg: logs.append(str(msg)),
            ):
                MODULE.setup_qbit_storage_defaults(
                    opener=object(),
                    qbit_url="http://qbittorrent:8080",
                    qbit_cfg=qbit_cfg,
                )

        whitelist = str(captured["prefs"].get("bypass_auth_subnet_whitelist") or "")
        self.assertIn("10.0.0.0/8", whitelist)
        self.assertNotIn("0.0.0.0/0", whitelist)
        self.assertTrue(
            any("refusing world-open auth bypass subnet" in line for line in logs),
            "Expected warning log when world-open subnet is filtered.",
        )


if __name__ == "__main__":
    unittest.main()
