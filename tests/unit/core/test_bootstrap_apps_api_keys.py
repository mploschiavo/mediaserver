import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

import media_stack.services.runtime_secrets as MODULE


class ApiKeyReadTests(unittest.TestCase):
    def test_read_api_key_prefers_env_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "SONARR_API_KEY": "from-env",
                    "BOOTSTRAP_APIKEY_FILE_TIMEOUT_SECONDS": "1",
                },
                clear=False,
            ):
                key = MODULE.read_api_key(tmp, "sonarr")

        self.assertEqual(key, "from-env")

    def test_read_api_key_reads_from_config_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / "sonarr"
            config_dir.mkdir(parents=True, exist_ok=True)
            (config_dir / "config.xml").write_text(
                "<Config><ApiKey>abc123</ApiKey></Config>", encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ,
                {"SONARR_API_KEY": "replace-after-first-boot"},
                clear=False,
            ):
                key = MODULE.read_api_key(tmp, "sonarr")

        self.assertEqual(key, "abc123")

    def test_read_api_key_times_out_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "BOOTSTRAP_APIKEY_FILE_TIMEOUT_SECONDS": "1",
                    "BOOTSTRAP_APIKEY_FILE_HEARTBEAT_SECONDS": "1",
                    "BOOTSTRAP_APIKEY_FILE_INTERVAL_SECONDS": "1",
                },
                clear=False,
            ):
                with self.assertRaises(RuntimeError):
                    MODULE.read_api_key(tmp, "sonarr")

    def test_read_api_key_falls_back_to_alt_config_root(self):
        with tempfile.TemporaryDirectory() as primary_tmp, tempfile.TemporaryDirectory() as alt_tmp:
            alt_config_dir = Path(alt_tmp) / "sonarr"
            alt_config_dir.mkdir(parents=True, exist_ok=True)
            (alt_config_dir / "config.xml").write_text(
                "<Config><ApiKey>alt-root-key</ApiKey></Config>", encoding="utf-8"
            )

            with mock.patch.dict(
                os.environ,
                {"BOOTSTRAP_ALT_CONFIG_ROOT": alt_tmp},
                clear=False,
            ):
                key = MODULE.read_api_key(primary_tmp, "sonarr")

        self.assertEqual(key, "alt-root-key")

    def test_read_api_key_jellyseerr_falls_back_to_alt_config_root(self):
        with tempfile.TemporaryDirectory() as primary_tmp, tempfile.TemporaryDirectory() as alt_tmp:
            alt_config_dir = Path(alt_tmp) / "jellyseerr"
            alt_config_dir.mkdir(parents=True, exist_ok=True)
            (alt_config_dir / "settings.json").write_text(
                '{"main":{"apiKey":"jellyseerr-alt-key"}}', encoding="utf-8"
            )

            with mock.patch.dict(
                os.environ,
                {"BOOTSTRAP_ALT_CONFIG_ROOT": alt_tmp},
                clear=False,
            ):
                key = MODULE.api_keys_service().read_api_key(
                    primary_tmp, "jellyseerr"
                )

        self.assertEqual(key, "jellyseerr-alt-key")


if __name__ == "__main__":
    unittest.main()
