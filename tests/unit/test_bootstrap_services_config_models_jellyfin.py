import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.jellyfin.config_models import (  # noqa: E402
    JellyfinLibrariesConfig,
    JellyfinPlaybackConfig,
    JellyfinPluginsConfig,
    JellyfinPrewarmConfig,
)


class JellyfinConfigModelTests(unittest.TestCase):
    def test_libraries_model_from_dict(self):
        model = JellyfinLibrariesConfig.from_dict(
            {
                "enabled": True,
                "required": True,
                "url": "http://jellyfin:8096",
                "libraries": [{"name": "Movies"}, "bad"],
                "tuning": {"enabled": True},
            }
        )
        self.assertTrue(model.enabled)
        self.assertTrue(model.required)
        self.assertEqual(model.url, "http://jellyfin:8096")
        self.assertEqual(model.libraries, [{"name": "Movies"}])
        self.assertEqual(model.tuning, {"enabled": True})

    def test_plugins_model_from_dict(self):
        model = JellyfinPluginsConfig.from_dict(
            {
                "enabled": True,
                "required": False,
                "repositories": [{"url": "https://repo.example"}, 7],
                "install": ["Intro Skipper"],
            }
        )
        self.assertTrue(model.enabled)
        self.assertFalse(model.required)
        self.assertEqual(model.repositories, [{"url": "https://repo.example"}])
        self.assertEqual(model.install, ["Intro Skipper"])

    def test_playback_model_from_dict(self):
        model = JellyfinPlaybackConfig.from_dict(
            {
                "enabled": True,
                "required": True,
                "user_defaults": {"SubtitleMode": "Smart"},
                "server_defaults": {"UICulture": "en-US"},
                "display_preferences": {"enabled": True},
            }
        )
        self.assertTrue(model.enabled)
        self.assertTrue(model.required)
        self.assertEqual(model.user_defaults["SubtitleMode"], "Smart")
        self.assertEqual(model.server_defaults["UICulture"], "en-US")
        self.assertTrue(model.display_preferences["enabled"])

    def test_prewarm_model_from_dict(self):
        model = JellyfinPrewarmConfig.from_dict(
            {
                "enabled": True,
                "required": False,
                "refresh_library": False,
                "refresh_channels": True,
                "refresh_guide": False,
                "book_sidecar_artwork": {"enabled": True},
                "music_sidecar_artwork": {"enabled": True},
                "metadata_backfill": {"enabled": True, "refresh_missing_overview": True},
                "artwork_health_check": {"enabled": True, "libraries": ["Books", "Music"]},
                "library_refresh_query": {"metadataRefreshMode": "FullRefresh"},
            }
        )
        self.assertTrue(model.enabled)
        self.assertFalse(model.required)
        self.assertFalse(model.refresh_library)
        self.assertTrue(model.refresh_channels)
        self.assertFalse(model.refresh_guide)
        self.assertTrue(model.book_sidecar_artwork["enabled"])
        self.assertTrue(model.music_sidecar_artwork["enabled"])
        self.assertTrue(model.metadata_backfill["enabled"])
        self.assertTrue(model.artwork_health_check["enabled"])
        self.assertEqual(model.library_refresh_query["metadataRefreshMode"], "FullRefresh")


if __name__ == "__main__":
    unittest.main()
