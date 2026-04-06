import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.api_keys_service import ApiKeysService  # noqa: E402


class ApiKeysServiceTests(unittest.TestCase):
    @staticmethod
    def _to_int(value, fallback):
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def _svc(self):
        return ApiKeysService(
            log=lambda _msg: None,
            to_int=self._to_int,
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            coerce_list=lambda value: value if isinstance(value, list) else [value],
            resolve_path=lambda root, rel: Path(root) / Path(str(rel)),
        )

    def test_read_api_key_ignores_placeholder_value(self):
        svc = self._svc()
        with mock.patch.dict(
            os.environ,
            {
                "SONARR_API_KEY": "replace-after-first-boot",
            },
            clear=False,
        ):
            self.assertEqual(svc.read_api_key_from_env("sonarr"), "")

    def test_read_api_key_from_config_xml(self):
        svc = self._svc()
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "sonarr" / "config.xml"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("<Config><ApiKey>abc123</ApiKey></Config>", encoding="utf-8")
            self.assertEqual(svc.read_api_key(tmp, "sonarr"), "abc123")

    def test_read_jellyfin_api_key_from_db_prefers_name(self):
        svc = self._svc()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "jellyfin" / "data" / "jellyfin.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE ApiKeys (Id INTEGER PRIMARY KEY, Name TEXT, AccessToken TEXT)"
                )
                conn.execute(
                    "INSERT INTO ApiKeys (Name, AccessToken) VALUES (?, ?)",
                    ("Other", "other-token"),
                )
                conn.execute(
                    "INSERT INTO ApiKeys (Name, AccessToken) VALUES (?, ?)",
                    ("Jellyfin", "preferred-token"),
                )
                conn.commit()
            finally:
                conn.close()

            token, source = svc.read_jellyfin_api_key_from_db(
                tmp,
                {
                    "api_key_db_path": "jellyfin/data/jellyfin.db",
                    "api_key_name_preference": ["Jellyfin"],
                },
            )
            self.assertEqual(token, "preferred-token")
            self.assertEqual(source, "jellyfin")


if __name__ == "__main__":
    unittest.main()
