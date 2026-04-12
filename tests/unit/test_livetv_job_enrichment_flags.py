"""Test that configure_livetv_job preserves enrichment flags from source guides.

Catches the bug where the merged guide dict hardcoded
enrich_program_icons_from_tuner_logo=False, discarding the source guide's
True value and causing 20k+ programmes to render as blue tiles.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_ctx(guides, tuners=None):
    """Build a minimal JobContext stub with jellyfin_livetv config."""
    ctx = MagicMock()
    ctx.media_server_id.return_value = "jellyfin"
    ctx.media_server_api_key.return_value = "test-key"
    ctx.config_root = "/tmp/test-config"
    ctx.wait_timeout = 10
    ctx.cfg = {
        "jellyfin_livetv": {
            "tuners": tuners or [{
                "name": "test-tuner",
                "type": "m3u",
                "url": "https://example.com/playlist.m3u",
                "materialized_output_path": "jellyfin/livetv-tuners/test.m3u",
            }],
            "guides": guides,
        },
    }
    return ctx


class TestMergedGuidePreservesEnrichmentFlags(unittest.TestCase):
    """The merged guide entry must carry forward flags from source guides."""

    @patch("media_stack.services.apps.jellyfin.configure_livetv_job.importlib")
    def _run_configure_with_guides(self, guides, mock_importlib):
        """Run configure_livetv with mocked merge and capture the guide dict."""
        # Mock the merge function to return a successful result
        mock_merge = MagicMock(return_value={
            "channels_with_programmes": 100,
            "programmes": 5000,
        })
        mock_module = MagicMock()
        mock_module.merge_epgs = mock_merge

        # Mock the livetv handler to capture what it receives
        captured_cfg = {}
        def capture_fn(cfg, config_root, wait_timeout):
            captured_cfg.update(cfg)

        mock_livetv_module = MagicMock()
        mock_livetv_module.ensure_jellyfin_livetv = capture_fn

        def import_side_effect(name):
            if "epg_merge" in name:
                return mock_module
            if "runtime_ops" in name:
                return mock_livetv_module
            return MagicMock()

        mock_importlib.import_module.side_effect = import_side_effect

        ctx = _make_ctx(guides)
        # Ensure the M3U file "exists" for the merge path check
        with patch.object(Path, "is_file", return_value=True):
            from media_stack.services.apps.jellyfin.configure_livetv_job import configure_livetv
            # Patch os.environ for API key and registry
            with patch.dict("os.environ", {"JELLYFIN_API_KEY": "test-key"}):
                configure_livetv(ctx)

        # Return the modified livetv config
        return ctx.cfg.get("jellyfin_livetv", {}).get("guides", [])

    def test_enrich_icons_true_preserved(self):
        """Source guide has enrich_program_icons_from_tuner_logo=True."""
        guides = [{
            "type": "xmltv",
            "path": "https://example.com/epg.xml",
            "enrich_program_icons_from_tuner_logo": True,
            "enrich_program_categories_from_tuner_groups": True,
            "default_program_icon_url": "https://example.com/default.png",
        }]
        result_guides = self._run_configure_with_guides(guides)
        self.assertTrue(len(result_guides) > 0, "No guides in result")
        merged = result_guides[0]
        self.assertTrue(
            merged.get("enrich_program_icons_from_tuner_logo"),
            f"enrich_program_icons_from_tuner_logo should be True, got: {merged}",
        )
        self.assertTrue(
            merged.get("enrich_program_categories_from_tuner_groups"),
            f"enrich_program_categories_from_tuner_groups should be True, got: {merged}",
        )

    def test_default_icon_url_preserved(self):
        """Source guide's default_program_icon_url must carry forward."""
        icon_url = "https://example.com/fallback-icon.png"
        guides = [{
            "type": "xmltv",
            "path": "https://example.com/epg.xml",
            "enrich_program_icons_from_tuner_logo": True,
            "default_program_icon_url": icon_url,
        }]
        result_guides = self._run_configure_with_guides(guides)
        self.assertTrue(len(result_guides) > 0)
        self.assertEqual(result_guides[0].get("default_program_icon_url"), icon_url)

    def test_category_lists_preserved(self):
        """Source guide's category lists must carry forward."""
        guides = [{
            "type": "xmltv",
            "path": "https://example.com/epg.xml",
            "enrich_program_icons_from_tuner_logo": True,
            "enrich_program_categories_from_tuner_groups": True,
            "movie_categories": ["Movie", "Film"],
            "sports_categories": ["Sports"],
            "kids_categories": ["Kids", "Family"],
            "news_categories": ["News"],
        }]
        result_guides = self._run_configure_with_guides(guides)
        self.assertTrue(len(result_guides) > 0)
        merged = result_guides[0]
        self.assertEqual(merged.get("movie_categories"), ["Movie", "Film"])
        self.assertEqual(merged.get("sports_categories"), ["Sports"])
        self.assertEqual(merged.get("kids_categories"), ["Kids", "Family"])
        self.assertEqual(merged.get("news_categories"), ["News"])

    def test_enrich_false_when_source_is_false(self):
        """If source guide says False, merged should also be False."""
        guides = [{
            "type": "xmltv",
            "path": "https://example.com/epg.xml",
            "enrich_program_icons_from_tuner_logo": False,
            "enrich_program_categories_from_tuner_groups": False,
        }]
        result_guides = self._run_configure_with_guides(guides)
        self.assertTrue(len(result_guides) > 0)
        merged = result_guides[0]
        self.assertFalse(merged.get("enrich_program_icons_from_tuner_logo"))
        self.assertFalse(merged.get("enrich_program_categories_from_tuner_groups"))

    def test_any_guide_true_wins(self):
        """Multiple source guides: if ANY has enrichment=True, merged should be True."""
        guides = [
            {
                "type": "xmltv",
                "path": "https://example.com/epg1.xml",
                "enrich_program_icons_from_tuner_logo": False,
            },
            {
                "type": "xmltv",
                "path": "https://example.com/epg2.xml",
                "enrich_program_icons_from_tuner_logo": True,
                "default_program_icon_url": "https://example.com/icon.png",
            },
        ]
        result_guides = self._run_configure_with_guides(guides)
        self.assertTrue(len(result_guides) > 0)
        self.assertTrue(result_guides[0].get("enrich_program_icons_from_tuner_logo"))


class TestMergedGuideStructure(unittest.TestCase):
    """The merged guide dict must have required fields for Jellyfin."""

    @patch("media_stack.services.apps.jellyfin.configure_livetv_job.importlib")
    def test_merged_guide_has_required_fields(self, mock_importlib):
        mock_merge = MagicMock(return_value={
            "channels_with_programmes": 50,
            "programmes": 1000,
        })
        mock_module = MagicMock()
        mock_module.merge_epgs = mock_merge
        mock_livetv_module = MagicMock()
        mock_livetv_module.ensure_jellyfin_livetv = MagicMock()

        def import_side_effect(name):
            if "epg_merge" in name:
                return mock_module
            if "runtime_ops" in name:
                return mock_livetv_module
            return MagicMock()

        mock_importlib.import_module.side_effect = import_side_effect

        ctx = _make_ctx([{
            "type": "xmltv",
            "path": "https://example.com/epg.xml",
            "enrich_program_icons_from_tuner_logo": True,
        }])
        with patch.object(Path, "is_file", return_value=True), \
             patch.dict("os.environ", {"JELLYFIN_API_KEY": "test-key"}):
            from media_stack.services.apps.jellyfin.configure_livetv_job import configure_livetv
            configure_livetv(ctx)

        guides = ctx.cfg["jellyfin_livetv"]["guides"]
        self.assertEqual(len(guides), 1)
        merged = guides[0]
        self.assertEqual(merged["type"], "xmltv")
        self.assertIn("/config/livetv-guides/merged-epg.xml", merged["path"])
        self.assertIn("materialized_output_path", merged)
        self.assertIn("enrich_program_icons_from_tuner_logo", merged)


if __name__ == "__main__":
    unittest.main()
