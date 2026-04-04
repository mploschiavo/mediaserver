"""Unit tests for bootstrap_api.preflight.api_keys discovery."""

import tempfile
import unittest
from pathlib import Path

from bootstrap_api.preflight.api_keys import (
    _read_bazarr_api_key,
    _read_ini_api_key,
    _read_xml_api_key,
    run_preflight,
)


class TestXmlApiKeyReader(unittest.TestCase):
    def test_reads_api_key_from_xml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write('<Config>\n  <ApiKey>abc123def456ghi789jkl012mno345pq</ApiKey>\n</Config>')
            f.flush()
            result = _read_xml_api_key(Path(f.name))
        self.assertEqual(result, "abc123def456ghi789jkl012mno345pq")

    def test_returns_empty_for_missing_file(self):
        result = _read_xml_api_key(Path("/nonexistent/config.xml"))
        self.assertEqual(result, "")

    def test_returns_empty_for_no_api_key(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
            f.write("<Config></Config>")
            f.flush()
            result = _read_xml_api_key(Path(f.name))
        self.assertEqual(result, "")


class TestIniApiKeyReader(unittest.TestCase):
    def test_reads_api_key_from_ini(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[misc]\napi_key = abcdef123456\nother = value\n")
            f.flush()
            result = _read_ini_api_key(Path(f.name))
        self.assertEqual(result, "abcdef123456")

    def test_returns_empty_for_missing(self):
        result = _read_ini_api_key(Path("/nonexistent/sab.ini"))
        self.assertEqual(result, "")


class TestBazarrApiKeyReader(unittest.TestCase):
    def test_reads_apikey_from_yaml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("auth:\n  apikey: my-bazarr-key-12345\n")
            f.flush()
            result = _read_bazarr_api_key(Path(f.name))
        self.assertEqual(result, "my-bazarr-key-12345")


class TestRunPreflight(unittest.TestCase):
    def test_discovers_keys_from_tree(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "sonarr").mkdir()
            (root / "sonarr" / "config.xml").write_text(
                "<Config><ApiKey>sonarr_key_32chars_padded_extra!</ApiKey></Config>"
            )
            (root / "sabnzbd").mkdir()
            (root / "sabnzbd" / "sabnzbd.ini").write_text("[misc]\napi_key = sab_key_here\n")

            result = run_preflight(config_root=str(root))
            self.assertIn("SONARR_API_KEY", result)
            self.assertEqual(result["SONARR_API_KEY"], "sonarr_key_32chars_padded_extra!")
            self.assertIn("SABNZBD_API_KEY", result)
            self.assertEqual(result["SABNZBD_API_KEY"], "sab_key_here")


if __name__ == "__main__":
    unittest.main()
