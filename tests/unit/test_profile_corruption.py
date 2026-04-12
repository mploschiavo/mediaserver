"""Tests for profile corruption prevention and data integrity.

These tests verify that:
1. Profile saves never lose critical fields (metadata.name)
2. Backups are created before overwrites
3. Invalid profiles are rejected before saving
4. The controller survives a corrupted profile without crashing
5. Metadata updates merge, not replace
6. update_profile_section preserves existing data
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.api.services.config as config_mod  # noqa: E402


def _make_profile(data, td):
    import yaml
    p = Path(td) / "profile.yaml"
    with open(p, "w") as f:
        yaml.dump(data, f)
    return str(p)


VALID_PROFILE = {
    "schema_version": 1,
    "kind": "media_stack_profile",
    "metadata": {"name": "test-stack", "platform": "compose", "purpose": "test"},
    "routing": {"gateway_host": "test.local"},
}


class TestProfileValidation(unittest.TestCase):
    def test_valid_profile_passes(self):
        err = config_mod._validate_profile_data(VALID_PROFILE)
        self.assertIsNone(err)

    def test_missing_metadata_name_fails(self):
        data = {"metadata": {"platform": "compose"}}
        err = config_mod._validate_profile_data(data)
        self.assertIsNotNone(err)
        self.assertIn("metadata.name", err)

    def test_no_metadata_section_fails(self):
        err = config_mod._validate_profile_data({"routing": {}})
        self.assertIsNotNone(err)

    def test_non_dict_fails(self):
        err = config_mod._validate_profile_data("not a dict")
        self.assertIsNotNone(err)

    def test_empty_name_fails(self):
        err = config_mod._validate_profile_data({"metadata": {"name": ""}})
        self.assertIsNotNone(err)


class TestSaveCreatesBackup(unittest.TestCase):
    def test_backup_created_on_save(self):
        with tempfile.TemporaryDirectory() as td:
            profile_path = Path(td) / "profile.yaml"
            profile_path.write_text("original content")
            config_mod._save_profile_yaml(VALID_PROFILE, profile_path)
            backup = profile_path.with_suffix(".yaml.bak")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(), "original content")

    def test_save_rejected_if_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            profile_path = Path(td) / "profile.yaml"
            profile_path.write_text("original")
            result = config_mod._save_profile_yaml({"no_metadata": True}, profile_path)
            self.assertIn("error", result)
            # Original file should be unchanged
            self.assertEqual(profile_path.read_text(), "original")


class TestMetadataUpdateMerges(unittest.TestCase):
    """Verify update_metadata_settings merges into metadata, doesn't replace."""

    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_preserves_metadata_name(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile(VALID_PROFILE, td)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile):
                result = config_mod.update_metadata_settings("de", "DE")
            self.assertEqual(result["status"], "saved")
            # Verify metadata.name is still there
            import yaml
            data = yaml.safe_load(Path(profile).read_text())
            self.assertEqual(data["metadata"]["name"], "test-stack")
            self.assertEqual(data["metadata"]["language"], "de")
            self.assertEqual(data["metadata"]["country"], "DE")

    def test_preserves_metadata_platform(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile(VALID_PROFILE, td)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile):
                config_mod.update_metadata_settings("fr", "FR")
            import yaml
            data = yaml.safe_load(Path(profile).read_text())
            self.assertEqual(data["metadata"]["platform"], "compose")

    def test_update_with_no_existing_metadata(self):
        """If metadata section doesn't exist, create it — but validation will fail."""
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile({"routing": {}}, td)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile):
                result = config_mod.update_metadata_settings("en", "US")
            # Should fail validation because metadata.name would be missing
            self.assertIn("error", result)


class TestProfileSectionUpdate(unittest.TestCase):
    """Verify update_profile_section preserves other sections."""

    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_preserves_routing(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile(VALID_PROFILE, td)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile):
                config_mod.update_profile_section("download_categories", {"tv": "/data/tv"})
            import yaml
            data = yaml.safe_load(Path(profile).read_text())
            self.assertEqual(data["routing"]["gateway_host"], "test.local")
            self.assertEqual(data["download_categories"]["tv"], "/data/tv")

    def test_preserves_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile(VALID_PROFILE, td)
            with patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile):
                config_mod.update_profile_section("live_tv_defaults", {"tuner_url": "http://example.com"})
            import yaml
            data = yaml.safe_load(Path(profile).read_text())
            self.assertEqual(data["metadata"]["name"], "test-stack")


class TestLiveTvSaveDoesNotCorrupt(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_livetv_save_preserves_metadata(self):
        """update_livetv_sources now saves to per-app config, not profile.
        Profile metadata must remain untouched."""
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile(VALID_PROFILE, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                config_mod.update_livetv_sources(
                    tuners=[{"url": "http://example.com/us.m3u", "name": "US"}],
                )
            import yaml
            data = yaml.safe_load(Path(profile).read_text())
            self.assertEqual(data["metadata"]["name"], "test-stack")

    def test_livetv_save_preserves_routing(self):
        """update_livetv_sources saves to per-app config — profile routing is untouched."""
        with tempfile.TemporaryDirectory() as td:
            profile = _make_profile(VALID_PROFILE, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                config_mod.update_livetv_sources(
                    tuners=[{"url": "http://example.com/de.m3u", "name": "DE"}],
                )
            import yaml
            data = yaml.safe_load(Path(profile).read_text())
            self.assertIn("routing", data)


class TestLibrarySaveDoesNotCorrupt(unittest.TestCase):
    def setUp(self):
        config_mod._invalidate_profile_cache()

    def tearDown(self):
        config_mod._invalidate_profile_cache()

    def test_library_save_preserves_metadata(self):
        """update_libraries saves to per-app config — profile metadata is untouched."""
        with tempfile.TemporaryDirectory() as td:
            data = {**VALID_PROFILE, "technology_bindings": {"media_server": "jellyfin"}}
            profile = _make_profile(data, td)
            with (
                patch("media_stack.api.services._resolve.resolve_profile_path", return_value=profile),
                patch.dict(os.environ, {"CONFIG_ROOT": td}),
            ):
                config_mod.update_libraries([
                    {"name": "Movies", "collection_type": "movies", "paths": ["/media/movies"]},
                ])
            import yaml
            saved = yaml.safe_load(Path(profile).read_text())
            self.assertEqual(saved["metadata"]["name"], "test-stack")


class TestControllerSurvivesCorruptProfile(unittest.TestCase):
    """The controller should start even with a corrupted profile."""

    def test_validate_profile_does_not_crash_server(self):
        """validate_profile failure should be caught, not crash the controller."""
        from media_stack.cli.commands.controller_serve import _run_serve
        # We can't easily test _run_serve end-to-end, but we can verify
        # that validate_profile raises RuntimeError which controller_serve catches
        from media_stack.api.preflight.profile_validation import validate_profile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("metadata:\n  platform: compose\n")  # Missing name
        try:
            with self.assertRaises(RuntimeError):
                validate_profile(f.name, log=lambda msg: None)
        finally:
            os.unlink(f.name)


if __name__ == "__main__":
    unittest.main()
