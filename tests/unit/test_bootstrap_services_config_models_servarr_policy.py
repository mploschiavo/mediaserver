import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.servarr.config_models import (  # noqa: E402
    ArrDownloadHandlingPolicy,
    ArrMediaManagementPolicy,
    ArrQualityUpgradePolicy,
    ServarrAppConfig,
)


class ServarrPolicyConfigModelTests(unittest.TestCase):
    def test_media_management_policy_resolves_per_app_override(self):
        policy = ArrMediaManagementPolicy.from_dict(
            {
                "enabled": True,
                "copy_using_hardlinks": True,
                "create_empty_series_folders": False,
                "by_app": {"Sonarr": {"create_empty_series_folders": True}},
            },
            canonicalize=lambda value: value.lower(),
        )
        app = ServarrAppConfig.from_dict(
            {"name": "TV", "implementation": "sonarr", "url": "http://sonarr", "root_folder": "/tv"}
        )
        resolved = policy.resolved_for(app, canonicalize=lambda value: value.lower())
        self.assertTrue(resolved.enabled)
        self.assertTrue(resolved.copy_using_hardlinks)
        self.assertTrue(resolved.create_empty_series_folders)

    def test_download_handling_policy_resolves_per_app_disable(self):
        policy = ArrDownloadHandlingPolicy.from_dict(
            {
                "enabled": True,
                "enable_completed_download_handling": True,
                "remove_completed_downloads": True,
                "remove_failed_downloads": True,
                "auto_redownload_failed": True,
                "by_app": {"readarr": {"enabled": False}},
            }
        )
        app = ServarrAppConfig.from_dict(
            {
                "name": "Readarr",
                "implementation": "readarr",
                "url": "http://readarr",
                "root_folder": "/books",
            }
        )
        resolved = policy.resolved_for(app)
        self.assertFalse(resolved.enabled)
        self.assertTrue(resolved.enable_completed_download_handling)

    def test_quality_upgrade_policy_resolves_override_lists(self):
        policy = ArrQualityUpgradePolicy.from_dict(
            {
                "enabled": True,
                "allow_upgrades": True,
                "disallow_quality_name_tokens": ["2160", "4k"],
                "cutoff_preferred_name_tokens": ["1080"],
                "by_app": {
                    "Radarr": {
                        "disallow_quality_name_tokens": ["cam"],
                        "cutoff_preferred_name_tokens": ["720"],
                    }
                },
            },
            canonicalize=lambda value: value.lower(),
        )
        app = ServarrAppConfig.from_dict(
            {
                "name": "Movies",
                "implementation": "radarr",
                "url": "http://radarr",
                "root_folder": "/movies",
            }
        )
        resolved = policy.resolved_for(app, canonicalize=lambda value: value.lower())
        self.assertEqual(resolved.disallow_quality_name_tokens, ["cam"])
        self.assertEqual(resolved.cutoff_preferred_name_tokens, ["720"])


if __name__ == "__main__":
    unittest.main()
