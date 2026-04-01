import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap_services.runtime_servarr.service_ops as MODULE
import bootstrap_services.runtime_servarr.factory as SERVARR_FACTORY


class ArrQualityUpgradePolicyTests(unittest.TestCase):
    def test_quality_upgrade_policy_updates_cutoff_and_disallowed_tiers(self):
        app_cfg = {"name": "Radarr", "implementation": "Radarr"}
        cfg = {"quality_profiles": {}}
        policy_cfg = {
            "enabled": True,
            "allow_upgrades": True,
            "disallow_quality_name_tokens": ["2160", "4k"],
            "cutoff_preferred_name_tokens": ["1080"],
        }
        selected_profile = {
            "id": 7,
            "cutoff": 99,
            "upgradeAllowed": False,
            "items": [
                {"quality": {"id": 1080, "name": "HD-1080p"}, "allowed": True},
                {"quality": {"id": 2160, "name": "Ultra-HD-2160p"}, "allowed": True},
            ],
        }

        captured = {}

        def fake_http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            captured["method"] = method
            captured["path"] = path
            captured["payload"] = payload
            return 200, {}, ""

        with (
            mock.patch.object(
                SERVARR_FACTORY, "resolve_arr_quality_preferences", return_value=(None, [])
            ),
            mock.patch.object(
                SERVARR_FACTORY, "get_arr_quality_profile", return_value=selected_profile
            ),
            mock.patch.object(SERVARR_FACTORY, "http_request", side_effect=fake_http_request),
        ):
            MODULE.ensure_arr_quality_upgrade_policy(
                cfg,
                app_cfg,
                "http://radarr:7878",
                "/api/v3",
                "key",
                policy_cfg,
            )

        self.assertEqual(captured.get("method"), "PUT")
        self.assertEqual(captured.get("path"), "/api/v3/qualityprofile/7")
        payload = captured.get("payload") or {}
        self.assertTrue(payload.get("upgradeAllowed"))
        self.assertEqual(payload.get("cutoff"), 1080)
        items = payload.get("items") or []
        self.assertTrue(items[0].get("allowed"))
        self.assertFalse(items[1].get("allowed"))

    def test_quality_upgrade_policy_noop_does_not_call_put(self):
        app_cfg = {"name": "Radarr", "implementation": "Radarr"}
        cfg = {"quality_profiles": {}}
        policy_cfg = {
            "enabled": True,
            "allow_upgrades": True,
            "disallow_quality_name_tokens": ["2160"],
            "cutoff_preferred_name_tokens": ["1080"],
        }
        selected_profile = {
            "id": 7,
            "cutoff": 1080,
            "upgradeAllowed": True,
            "items": [
                {"quality": {"id": 1080, "name": "HD-1080p"}, "allowed": True},
                {"quality": {"id": 2160, "name": "Ultra-HD-2160p"}, "allowed": False},
            ],
        }

        with (
            mock.patch.object(
                SERVARR_FACTORY, "resolve_arr_quality_preferences", return_value=(None, [])
            ),
            mock.patch.object(
                SERVARR_FACTORY, "get_arr_quality_profile", return_value=selected_profile
            ),
            mock.patch.object(SERVARR_FACTORY, "http_request") as request_mock,
        ):
            MODULE.ensure_arr_quality_upgrade_policy(
                cfg,
                app_cfg,
                "http://radarr:7878",
                "/api/v3",
                "key",
                policy_cfg,
            )

        request_mock.assert_not_called()


class MediaHygieneHelperTests(unittest.TestCase):
    def test_queue_item_failure_detection(self):
        item = {
            "status": "completed",
            "trackedDownloadStatus": "ImportFailed",
            "statusMessages": [{"title": "Import failed", "messages": ["permission denied"]}],
        }
        tokens = ["failed", "error", "importfailed"]
        self.assertTrue(MODULE.queue_item_is_failed(item, tokens))

        healthy = {"status": "completed", "trackedDownloadStatus": "ok"}
        self.assertFalse(MODULE.queue_item_is_failed(healthy, tokens))

    def test_filesystem_hygiene_removes_temp_zero_and_duplicate_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            d1 = root / "a"
            d2 = root / "b"
            d1.mkdir(parents=True, exist_ok=True)
            d2.mkdir(parents=True, exist_ok=True)

            zero = d1 / "zero.tmp"
            zero.write_bytes(b"")

            temp_file = d1 / "unfinished.part"
            temp_file.write_bytes(b"partial")

            dup_new = d1 / "movie.mkv"
            dup_old = d2 / "movie.mkv"
            content = b"x" * 1024
            dup_new.write_bytes(content)
            dup_old.write_bytes(content)
            os.utime(dup_old, (dup_old.stat().st_atime - 3600, dup_old.stat().st_mtime - 3600))

            cfg = {
                "filesystem": {
                    "enabled": True,
                    "roots": [str(root)],
                    "min_file_age_hours": 0,
                    "remove_zero_byte_files": True,
                    "temp_extensions": [".part", ".tmp"],
                    "remove_empty_dirs": True,
                    "dedupe": {
                        "enabled": True,
                        "dry_run": False,
                        "max_delete_per_run": 10,
                        "min_size_bytes": 1,
                    },
                }
            }

            result = MODULE.run_filesystem_hygiene(cfg)

            self.assertGreaterEqual(result.get("removed_zero", 0), 1)
            self.assertGreaterEqual(result.get("removed_temp", 0), 1)
            self.assertGreaterEqual(result.get("removed_dupes", 0), 1)
            self.assertFalse(zero.exists())
            self.assertFalse(temp_file.exists())
            self.assertEqual(sum(1 for p in [dup_new, dup_old] if p.exists()), 1)


if __name__ == "__main__":
    unittest.main()
