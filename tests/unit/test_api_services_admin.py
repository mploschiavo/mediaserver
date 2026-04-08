"""Unit tests for media_stack.api.services.admin — key read/write helpers and high-level ops."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.services.admin import (  # noqa: E402
    _read_key_xml,
    _write_key_xml,
    _read_key_ini,
    _write_key_ini,
    _read_key_yaml,
    _write_key_yaml,
    _read_key_json,
    _write_key_json,
    _read_key,
)


# ---------------------------------------------------------------------------
# XML format (Sonarr / Radarr config.xml)
# ---------------------------------------------------------------------------

class TestReadKeyXml(unittest.TestCase):
    def test_reads_api_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write('<Config>\n  <ApiKey>abc123def456</ApiKey>\n</Config>')
            f.flush()
            result = _read_key_xml(Path(f.name))
        self.assertEqual(result, "abc123def456")
        os.unlink(f.name)

    def test_returns_empty_for_missing_file(self):
        result = _read_key_xml(Path("/nonexistent/config.xml"))
        self.assertEqual(result, "")

    def test_returns_empty_when_no_apikey_element(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config><Port>8989</Port></Config>")
            f.flush()
            result = _read_key_xml(Path(f.name))
        self.assertEqual(result, "")
        os.unlink(f.name)

    def test_strips_whitespace_around_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config><ApiKey>  spaced_key  </ApiKey></Config>")
            f.flush()
            result = _read_key_xml(Path(f.name))
        self.assertEqual(result, "spaced_key")
        os.unlink(f.name)


class TestWriteKeyXml(unittest.TestCase):
    def test_replaces_existing_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config>\n  <ApiKey>old_key</ApiKey>\n</Config>")
            f.flush()
            path = Path(f.name)
        _write_key_xml(path, "brand_new_key")
        content = path.read_text()
        self.assertIn("<ApiKey>brand_new_key</ApiKey>", content)
        self.assertNotIn("old_key", content)
        os.unlink(path)

    def test_preserves_surrounding_xml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config>\n  <Port>8989</Port>\n  <ApiKey>original</ApiKey>\n  <LogLevel>info</LogLevel>\n</Config>")
            f.flush()
            path = Path(f.name)
        _write_key_xml(path, "rotated")
        content = path.read_text()
        self.assertIn("<Port>8989</Port>", content)
        self.assertIn("<LogLevel>info</LogLevel>", content)
        self.assertIn("<ApiKey>rotated</ApiKey>", content)
        os.unlink(path)


# ---------------------------------------------------------------------------
# INI format (SABnzbd sabnzbd.ini)
# ---------------------------------------------------------------------------

class TestReadKeyIni(unittest.TestCase):
    def test_reads_api_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[misc]\napi_key = sab_key_abc123\nother = value\n")
            f.flush()
            result = _read_key_ini(Path(f.name))
        self.assertEqual(result, "sab_key_abc123")
        os.unlink(f.name)

    def test_returns_empty_for_missing_file(self):
        result = _read_key_ini(Path("/nonexistent/sabnzbd.ini"))
        self.assertEqual(result, "")

    def test_returns_empty_when_no_api_key_line(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[misc]\ndownload_dir = /data\n")
            f.flush()
            result = _read_key_ini(Path(f.name))
        self.assertEqual(result, "")
        os.unlink(f.name)

    def test_handles_leading_whitespace(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[misc]\n  api_key = indented_key\n")
            f.flush()
            result = _read_key_ini(Path(f.name))
        self.assertEqual(result, "indented_key")
        os.unlink(f.name)


class TestWriteKeyIni(unittest.TestCase):
    def test_replaces_existing_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[misc]\napi_key = old_sab_key\ndownload_dir = /data\n")
            f.flush()
            path = Path(f.name)
        _write_key_ini(path, "new_sab_key")
        content = path.read_text()
        self.assertIn("api_key = new_sab_key", content)
        self.assertNotIn("old_sab_key", content)
        self.assertIn("download_dir = /data", content)
        os.unlink(path)


# ---------------------------------------------------------------------------
# YAML format (Bazarr config.yaml)
# ---------------------------------------------------------------------------

class TestReadKeyYaml(unittest.TestCase):
    def test_reads_apikey(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("auth:\n  apikey: bazarr_key_xyz\n  type: basic\n")
            f.flush()
            result = _read_key_yaml(Path(f.name))
        self.assertEqual(result, "bazarr_key_xyz")
        os.unlink(f.name)

    def test_returns_empty_for_missing_file(self):
        result = _read_key_yaml(Path("/nonexistent/config.yaml"))
        self.assertEqual(result, "")

    def test_returns_empty_when_no_apikey_line(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("auth:\n  type: none\n")
            f.flush()
            result = _read_key_yaml(Path(f.name))
        self.assertEqual(result, "")
        os.unlink(f.name)

    def test_reads_quoted_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("auth:\n  apikey: 'quoted_key_123'\n")
            f.flush()
            result = _read_key_yaml(Path(f.name))
        self.assertEqual(result, "quoted_key_123")
        os.unlink(f.name)


class TestWriteKeyYaml(unittest.TestCase):
    def test_writes_new_key_into_existing_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("auth:\n  apikey: old_yaml_key\n  type: basic\n")
            f.flush()
            path = Path(f.name)
        _write_key_yaml(path, "new_yaml_key")
        import yaml
        with open(path) as fh:
            data = yaml.safe_load(fh)
        self.assertEqual(data["auth"]["apikey"], "new_yaml_key")
        os.unlink(path)

    def test_creates_auth_section_if_missing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("general:\n  port: 6767\n")
            f.flush()
            path = Path(f.name)
        _write_key_yaml(path, "fresh_key")
        import yaml
        with open(path) as fh:
            data = yaml.safe_load(fh)
        self.assertEqual(data["auth"]["apikey"], "fresh_key")
        self.assertEqual(data["general"]["port"], 6767)
        os.unlink(path)


# ---------------------------------------------------------------------------
# JSON format (Jellyseerr settings.json)
# ---------------------------------------------------------------------------

class TestReadKeyJson(unittest.TestCase):
    def test_reads_apikey(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"main": {"apiKey": "json_key_abc"}}, f)
            f.flush()
            result = _read_key_json(Path(f.name))
        self.assertEqual(result, "json_key_abc")
        os.unlink(f.name)

    def test_returns_empty_for_missing_file(self):
        result = _read_key_json(Path("/nonexistent/settings.json"))
        self.assertEqual(result, "")

    def test_returns_empty_for_malformed_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json {{{")
            f.flush()
            result = _read_key_json(Path(f.name))
        self.assertEqual(result, "")
        os.unlink(f.name)

    def test_returns_empty_when_main_section_absent(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"other": {"field": "value"}}, f)
            f.flush()
            result = _read_key_json(Path(f.name))
        self.assertEqual(result, "")
        os.unlink(f.name)


class TestWriteKeyJson(unittest.TestCase):
    def test_replaces_existing_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"main": {"apiKey": "old_json_key", "port": 5055}}, f)
            f.flush()
            path = Path(f.name)
        _write_key_json(path, "new_json_key")
        data = json.loads(path.read_text())
        self.assertEqual(data["main"]["apiKey"], "new_json_key")
        self.assertEqual(data["main"]["port"], 5055)
        os.unlink(path)


# ---------------------------------------------------------------------------
# _read_key() dispatcher
# ---------------------------------------------------------------------------

class TestReadKeyDispatcher(unittest.TestCase):
    def test_dispatches_to_xml_reader(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = Path(tmpdir) / "sonarr" / "config.xml"
            cfg.parent.mkdir()
            cfg.write_text("<Config><ApiKey>dispatched_key</ApiKey></Config>")

            svc = mock.MagicMock()
            svc.api_key_format = "xml"
            svc.api_key_config = "sonarr/config.xml"

            result = _read_key(svc, tmpdir)
            self.assertEqual(result, "dispatched_key")

    def test_returns_empty_for_unknown_format(self):
        svc = mock.MagicMock()
        svc.api_key_format = "toml"
        svc.api_key_config = "some/path"
        result = _read_key(svc, "/tmp")
        self.assertEqual(result, "")

    def test_returns_empty_when_no_config_path(self):
        svc = mock.MagicMock()
        svc.api_key_format = "xml"
        svc.api_key_config = ""
        result = _read_key(svc, "/tmp")
        self.assertEqual(result, "")


# ---------------------------------------------------------------------------
# rotate_keys() — high-level, mocked dependencies
# ---------------------------------------------------------------------------

class TestRotateKeys(unittest.TestCase):
    @mock.patch("media_stack.api.services.admin.restart_service")
    @mock.patch("media_stack.api.services.admin.persist_keys_to_secret")
    @mock.patch("media_stack.api.services.admin.get_services_with_api_keys")
    def test_rotates_file_based_keys(self, mock_get_svcs, mock_persist, mock_restart):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up a fake XML config
            cfg_dir = Path(tmpdir) / "sonarr"
            cfg_dir.mkdir()
            cfg_file = cfg_dir / "config.xml"
            cfg_file.write_text("<Config><ApiKey>old_key_here</ApiKey></Config>")

            svc = mock.MagicMock()
            svc.id = "sonarr"
            svc.api_key_config = "sonarr/config.xml"
            svc.api_key_format = "xml"
            svc.api_key_env = "SONARR_API_KEY"
            mock_get_svcs.return_value = [svc]

            os.environ["CONFIG_ROOT"] = tmpdir
            try:
                from media_stack.api.services.admin import rotate_keys
                result = rotate_keys()
            finally:
                os.environ.pop("CONFIG_ROOT", None)
                os.environ.pop("SONARR_API_KEY", None)

            self.assertEqual(result["status"], "rotated")
            self.assertIn("SONARR_API_KEY", result["keys"])
            self.assertEqual(result["errors"], [])
            mock_persist.assert_called_once()
            # Verify the file was actually written with a new key
            new_content = cfg_file.read_text()
            self.assertNotIn("old_key_here", new_content)
            self.assertIn("<ApiKey>", new_content)


# ---------------------------------------------------------------------------
# reset_password() — high-level, mocked network calls
# ---------------------------------------------------------------------------

class TestResetPassword(unittest.TestCase):
    @mock.patch("media_stack.api.services.admin.restart_service")
    @mock.patch("media_stack.api.services.admin.persist_keys_to_secret")
    @mock.patch("media_stack.api.services.admin.get_services_with_password_config")
    @mock.patch("media_stack.api.services.admin.get_services_with_password_api")
    @mock.patch("media_stack.api.services.admin.SERVICE_MAP", {})
    def test_updates_ini_password_config(self, mock_pw_api, mock_pw_config, mock_persist, mock_restart):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up a fake INI config for SABnzbd
            cfg_dir = Path(tmpdir) / "sabnzbd"
            cfg_dir.mkdir()
            cfg_file = cfg_dir / "sabnzbd.ini"
            cfg_file.write_text(
                "[misc]\nusername = admin\npassword = old_pass\ndownload_dir = /data\n"
            )

            svc = mock.MagicMock()
            svc.id = "sabnzbd"
            svc.password_config = "sabnzbd/sabnzbd.ini"
            mock_pw_api.return_value = []
            mock_pw_config.return_value = [svc]

            os.environ["CONFIG_ROOT"] = tmpdir
            os.environ.pop("K8S_NAMESPACE", None)
            try:
                from media_stack.api.services.admin import reset_password
                result = reset_password("new_secure_pass")
            finally:
                os.environ.pop("CONFIG_ROOT", None)
                os.environ.pop("STACK_ADMIN_PASSWORD", None)

            self.assertEqual(result["status"], "updated")
            self.assertIn("sabnzbd", result["services"])
            content = cfg_file.read_text()
            self.assertIn("password = new_secure_pass", content)
            self.assertIn("username = admin", content)
            self.assertIn("download_dir = /data", content)

    @mock.patch("media_stack.api.services.admin.restart_service")
    @mock.patch("media_stack.api.services.admin.persist_keys_to_secret")
    @mock.patch("media_stack.api.services.admin.get_services_with_password_config")
    @mock.patch("media_stack.api.services.admin.get_services_with_password_api")
    @mock.patch("media_stack.api.services.admin.SERVICE_MAP", {})
    def test_returns_updated_with_no_services(self, mock_pw_api, mock_pw_config, mock_persist, mock_restart):
        mock_pw_api.return_value = []
        mock_pw_config.return_value = []
        os.environ.pop("K8S_NAMESPACE", None)
        try:
            from media_stack.api.services.admin import reset_password
            result = reset_password("any_pass")
        finally:
            os.environ.pop("STACK_ADMIN_PASSWORD", None)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["services"], [])
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
