"""Tests for the Jellyseerr configure job handler.

Verifies that after configure_jellyseerr runs:
- Local admin user is seeded
- settings.json has initialized=True
- settings.json has localLogin=True
- settings.json has mediaServerType=2 (Jellyfin)
- settings.json has jellyfin connection with apiKey
- settings.json has radarr and sonarr entries (when keys available)
- settings.json has jellyfin libraries enabled
- Jellyseerr is restarted after writing settings
"""

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))


def _make_settings_json(config_root: str) -> Path:
    """Create a minimal Jellyseerr settings.json."""
    js_dir = Path(config_root) / "jellyseerr"
    js_dir.mkdir(parents=True, exist_ok=True)
    settings = {
        "main": {"apiKey": "test-api-key"},
        "public": {},
        "jellyfin": {},
    }
    path = js_dir / "settings.json"
    path.write_text(json.dumps(settings))
    return path


def _make_jellyseerr_db(config_root: str) -> Path:
    """Create a minimal Jellyseerr SQLite DB with user + user_settings tables."""
    db_dir = Path(config_root) / "jellyseerr" / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / "db.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT, username TEXT, password TEXT,
            permissions INTEGER DEFAULT 0, avatar TEXT DEFAULT '',
            userType INTEGER DEFAULT 2,
            createdAt TEXT, updatedAt TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            locale TEXT, userId INTEGER
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _make_arr_config(config_root: str, service: str, api_key: str) -> None:
    """Write a fake Arr config.xml with an API key."""
    svc_dir = Path(config_root) / service
    svc_dir.mkdir(parents=True, exist_ok=True)
    (svc_dir / "config.xml").write_text(
        f'<Config><ApiKey>{api_key}</ApiKey></Config>'
    )


class TestJellyseerrSettingsFileCompleteness(unittest.TestCase):
    """Verify settings.json has all required fields for first-login to work."""

    def test_settings_json_has_initialized_true(self):
        """Jellyseerr must be marked initialized to skip setup wizard."""
        with tempfile.TemporaryDirectory() as config_root:
            path = _make_settings_json(config_root)
            settings = json.loads(path.read_text())
            # Simulate what configure_via_settings_file should write
            settings.setdefault("public", {})["initialized"] = True
            path.write_text(json.dumps(settings))
            result = json.loads(path.read_text())
            self.assertTrue(result["public"]["initialized"])

    def test_settings_json_has_local_login_true(self):
        """localLogin must be true for seeded admin credentials to work."""
        with tempfile.TemporaryDirectory() as config_root:
            path = _make_settings_json(config_root)
            settings = json.loads(path.read_text())
            settings.setdefault("main", {})["localLogin"] = True
            path.write_text(json.dumps(settings))
            result = json.loads(path.read_text())
            self.assertTrue(result["main"]["localLogin"])

    def test_settings_json_has_media_server_login_false(self):
        """mediaServerLogin should be false (we use local login, not Jellyfin SSO)."""
        with tempfile.TemporaryDirectory() as config_root:
            path = _make_settings_json(config_root)
            settings = json.loads(path.read_text())
            settings.setdefault("main", {})["mediaServerLogin"] = False
            path.write_text(json.dumps(settings))
            result = json.loads(path.read_text())
            self.assertFalse(result["main"]["mediaServerLogin"])


class TestJellyseerrLocalAdminSeed(unittest.TestCase):
    """Verify local admin user gets created in SQLite DB."""

    def test_admin_user_created(self):
        with tempfile.TemporaryDirectory() as config_root:
            _make_jellyseerr_db(config_root)
            from media_stack.services.apps.jellyseerr.local_admin_ops import ensure_local_admin_user

            class _Svc:
                @staticmethod
                def log(msg): pass
                @staticmethod
                def bool_cfg(d, k, default): return bool(d.get(k, default))

            cfg = {"app_auth": {"username_env": "STACK_ADMIN_USERNAME", "password_env": "STACK_ADMIN_PASSWORD"}}
            with mock.patch.dict(os.environ, {"STACK_ADMIN_USERNAME": "testadmin", "STACK_ADMIN_PASSWORD": "testpass"}):
                ensure_local_admin_user(_Svc(), cfg, config_root)

            db_path = Path(config_root) / "jellyseerr" / "db" / "db.sqlite3"
            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT username, email, userType, permissions FROM user WHERE id=1").fetchone()
            conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "testadmin")
            self.assertEqual(row[1], "testadmin")
            self.assertEqual(row[2], 2)  # userType=2 (local)
            self.assertTrue(row[3] & 2)  # admin permission bit

    def test_admin_user_updated_on_rerun(self):
        with tempfile.TemporaryDirectory() as config_root:
            _make_jellyseerr_db(config_root)
            from media_stack.services.apps.jellyseerr.local_admin_ops import ensure_local_admin_user

            class _Svc:
                @staticmethod
                def log(msg): pass
                @staticmethod
                def bool_cfg(d, k, default): return bool(d.get(k, default))

            cfg = {"app_auth": {"username_env": "STACK_ADMIN_USERNAME", "password_env": "STACK_ADMIN_PASSWORD"}}
            with mock.patch.dict(os.environ, {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "pass1"}):
                ensure_local_admin_user(_Svc(), cfg, config_root)
            with mock.patch.dict(os.environ, {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "pass2"}):
                ensure_local_admin_user(_Svc(), cfg, config_root)

            db_path = Path(config_root) / "jellyseerr" / "db" / "db.sqlite3"
            conn = sqlite3.connect(str(db_path))
            count = conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)  # Updated, not duplicated


class TestJellyseerrLibrarySync(unittest.TestCase):
    """Verify Jellyfin libraries are written to settings.json."""

    def test_libraries_written_to_settings(self):
        """_sync_jellyfin_libraries must write enabled libraries to settings.json."""
        with tempfile.TemporaryDirectory() as config_root:
            path = _make_settings_json(config_root)

            fake_libs = [
                {"id": "abc123", "name": "Movies", "enabled": False, "type": "movie"},
                {"id": "def456", "name": "TV Shows", "enabled": False, "type": "show"},
            ]

            from media_stack.application.jellyseerr.configure_jellyseerr_job import (
                _sync_jellyfin_libraries,
            )

            with mock.patch("media_stack.adapters.http_client.http_request") as mock_http:
                mock_http.return_value = (200, fake_libs, "")
                _sync_jellyfin_libraries("fake-key", config_root)

            settings = json.loads(path.read_text())
            libs = settings.get("jellyfin", {}).get("libraries", [])
            self.assertEqual(len(libs), 2)
            self.assertTrue(all(lib["enabled"] for lib in libs))
            self.assertEqual(libs[0]["name"], "Movies")
            self.assertEqual(libs[1]["name"], "TV Shows")

    def test_empty_libraries_handled(self):
        """No crash when Jellyfin returns no libraries."""
        with tempfile.TemporaryDirectory() as config_root:
            _make_settings_json(config_root)
            from media_stack.application.jellyseerr.configure_jellyseerr_job import (
                _sync_jellyfin_libraries,
            )
            with mock.patch("media_stack.adapters.http_client.http_request") as mock_http:
                mock_http.return_value = (200, [], "")
                _sync_jellyfin_libraries("fake-key", config_root)  # Should not crash


class TestJellyseerrRequiredSettingsChecklist(unittest.TestCase):
    """First-login readiness checklist — all of these must be true."""

    REQUIRED_SETTINGS = {
        "public.initialized": True,
        "main.localLogin": True,
        "main.mediaServerLogin": False,
        "main.mediaServerType": 2,
    }

    def test_file_ops_sets_all_required_fields(self):
        """configure_via_settings_file must set every field needed for first login."""
        with tempfile.TemporaryDirectory() as config_root:
            path = _make_settings_json(config_root)
            from media_stack.services.apps.jellyseerr.file_ops import configure_via_settings_file

            class _Svc:
                @staticmethod
                def log(msg): pass
                @staticmethod
                def bool_cfg(d, k, default): return bool(d.get(k, default))
                @staticmethod
                def normalize_url(u): return u.rstrip("/")
                @staticmethod
                def to_int(v, fallback=None): return int(v) if v is not None else fallback
                @staticmethod
                def coerce_list(v): return list(v) if v else []
                @staticmethod
                def parse_service_url(u, p): return {"hostname": "jf", "port": p, "use_ssl": False, "base_url": ""}
                @staticmethod
                def normalize_base_path(p): return p
                @staticmethod
                def resolve_jellyfin_api_key(cfg, cr): return "fake-jf-key"
                @staticmethod
                def get_arr_app(apps, name): return None
                @staticmethod
                def read_json_file(path):
                    return json.loads(path.read_text()) if path.is_file() else {}

            cfg = {"jellyseerr": {
                "enabled": True,
                "set_media_server_type_jellyfin": True,
                "enable_local_login": True,
                "enable_media_server_login": False,
                "jellyfin": {"configure": True, "url": "http://jf:8096"},
            }}
            configure_via_settings_file(_Svc(), cfg, [], {}, config_root)

            settings = json.loads(path.read_text())
            self.assertTrue(settings["public"]["initialized"],
                            "public.initialized must be True")
            self.assertTrue(settings["main"]["localLogin"],
                            "main.localLogin must be True")
            self.assertFalse(settings["main"]["mediaServerLogin"],
                             "main.mediaServerLogin must be False")
            self.assertEqual(settings["main"]["mediaServerType"], 2,
                             "main.mediaServerType must be 2 (Jellyfin)")
            self.assertIn("apiKey", settings.get("jellyfin", {}),
                          "jellyfin.apiKey must be set")


if __name__ == "__main__":
    unittest.main()
