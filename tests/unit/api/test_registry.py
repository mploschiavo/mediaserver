"""Tests for service registry: loading, lookup helpers, key readers."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.registry import (  # noqa: E402
    SERVICES, SERVICE_MAP, ServiceDef, _parse_service_entry,
    get_service, get_services_with_api_keys, get_services_with_password_api,
    get_active_service_ids, get_web_ui_services, KEY_READERS,
    read_api_key_from_file,
)
from media_stack.api.services.key_formats import (  # noqa: E402
    read_xml as _read_key_xml, read_ini as _read_key_ini,
    read_yaml as _read_key_yaml, read_json as _read_key_json,
)


class TestServiceDef(unittest.TestCase):
    def test_creation_defaults(self):
        s = ServiceDef(id="test", name="Test")
        self.assertEqual(s.id, "test")
        self.assertEqual(s.category, "management")
        self.assertTrue(s.web_ui)

    def test_frozen(self):
        s = ServiceDef(id="test", name="Test")
        with self.assertRaises(AttributeError):
            s.id = "changed"

    def test_custom_fields(self):
        s = ServiceDef(id="x", name="X", port=8080, category="media")
        self.assertEqual(s.port, 8080)
        self.assertEqual(s.category, "media")


class TestParseServiceEntry(unittest.TestCase):
    def test_valid_entry(self):
        entry = {"id": "sonarr", "name": "Sonarr", "port": 8989}
        result = _parse_service_entry(entry)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, "sonarr")

    def test_missing_id_returns_none(self):
        self.assertIsNone(_parse_service_entry({"name": "X"}))

    def test_empty_dict_returns_none(self):
        self.assertIsNone(_parse_service_entry({}))

    def test_not_dict_returns_none(self):
        self.assertIsNone(_parse_service_entry("string"))

    def test_profiles_as_string(self):
        entry = {"id": "x", "profiles": "full"}
        result = _parse_service_entry(entry)
        self.assertEqual(result.profiles, ["full"])


class TestLookupHelpers(unittest.TestCase):
    def test_get_service_found(self):
        if SERVICES:
            s = get_service(SERVICES[0].id)
            self.assertIsNotNone(s)

    def test_get_service_missing(self):
        self.assertIsNone(get_service("nonexistent-service-xyz"))

    def test_services_with_api_keys(self):
        result = get_services_with_api_keys()
        for s in result:
            self.assertTrue(s.api_key_env)

    def test_services_with_password_api(self):
        result = get_services_with_password_api()
        for s in result:
            self.assertTrue(s.password_api_path)

    def test_active_service_ids(self):
        result = get_active_service_ids()
        self.assertIsInstance(result, set)

    def test_web_ui_services(self):
        result = get_web_ui_services()
        for s in result:
            self.assertTrue(s.web_ui)

    def test_service_map_matches_services(self):
        self.assertEqual(len(SERVICE_MAP), len(SERVICES))


class TestKeyReaders(unittest.TestCase):
    def test_all_formats_registered(self):
        for fmt in ("xml", "ini", "yaml", "json", "sqlite"):
            self.assertIn(fmt, KEY_READERS)

    def test_read_xml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config><ApiKey>abc123</ApiKey></Config>")
            f.flush()
            result = _read_key_xml(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "abc123")

    def test_read_ini(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[main]\napi_key = def456\n")
            f.flush()
            result = _read_key_ini(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "def456")

    def test_read_yaml_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("apikey: ghi789\n")
            f.flush()
            result = _read_key_yaml(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "ghi789")

    def test_read_json_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"main": {"apiKey": "jkl012"}}, f)
            f.flush()
            result = _read_key_json(Path(f.name))
        os.unlink(f.name)
        self.assertEqual(result, "jkl012")

    def test_read_api_key_from_file_unknown_format(self):
        result = read_api_key_from_file("nonexistent-service", "/tmp")
        self.assertEqual(result, "")

    def test_read_api_key_missing_file(self):
        # A service with xml format but file doesn't exist
        with patch.dict(SERVICE_MAP, {"fake": ServiceDef(
                id="fake", name="Fake", api_key_config="fake/config.xml",
                api_key_format="xml", api_key_env="FAKE_API_KEY")}):
            result = read_api_key_from_file("fake", "/nonexistent")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
