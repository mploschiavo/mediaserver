"""Tests for per-app configuration service."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.app_config_service import (
    load_app_config, save_app_config, update_app_config_section,
    get_merged_app_config,
)


class TestLoadAppConfig(unittest.TestCase):
    def test_returns_empty_when_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                result = load_app_config("nonexistent")
        self.assertEqual(result, {})

    def test_loads_existing_config(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_dir = Path(td) / "jellyfin"
            cfg_dir.mkdir()
            (cfg_dir / "controller.yaml").write_text("livetv:\n  tuners: []\n")
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                result = load_app_config("jellyfin")
        self.assertIn("livetv", result)
        self.assertEqual(result["livetv"]["tuners"], [])


class TestSaveAppConfig(unittest.TestCase):
    def test_creates_directory_and_file(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                result = save_app_config("jellyfin", {"livetv": {"tuners": []}})
            self.assertEqual(result["status"], "saved")
            self.assertTrue((Path(td) / "jellyfin" / "controller.yaml").is_file())

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                save_app_config("test", {"key": "old"})
                save_app_config("test", {"key": "new"})
                result = load_app_config("test")
            self.assertEqual(result["key"], "new")


class TestUpdateSection(unittest.TestCase):
    def test_updates_single_section(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                save_app_config("svc", {"existing": True})
                update_app_config_section("svc", "new_section", {"data": 1})
                result = load_app_config("svc")
            self.assertTrue(result["existing"])
            self.assertEqual(result["new_section"]["data"], 1)


class TestMergedConfig(unittest.TestCase):
    def test_user_overrides_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                save_app_config("svc", {"key": "user_value"})
                result = get_merged_app_config("svc", {"key": "default", "other": "kept"})
            self.assertEqual(result["key"], "user_value")
            self.assertEqual(result["other"], "kept")

    def test_defaults_only_when_no_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                result = get_merged_app_config("svc", {"key": "default"})
            self.assertEqual(result["key"], "default")


class TestEdgeCases(unittest.TestCase):
    def test_empty_service_id(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                self.assertEqual(load_app_config(""), {})

    def test_special_characters_in_values(self):
        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.dict(os.environ, {"CONFIG_ROOT": td}):
                save_app_config("svc", {"url": "https://example.com/path?q=1&b=2"})
                result = load_app_config("svc")
            self.assertEqual(result["url"], "https://example.com/path?q=1&b=2")


if __name__ == "__main__":
    unittest.main()
