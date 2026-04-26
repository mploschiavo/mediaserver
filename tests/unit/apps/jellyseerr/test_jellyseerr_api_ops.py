import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyseerr.api_ops import (  # noqa: E402
    ensure_jellyfin_settings,
    ensure_main_settings,
    ensure_radarr,
    ensure_sonarr,
)

# ---------------------------------------------------------------------------
# Stub ``svc`` that mimics the bootstrap service interface used by api_ops
# ---------------------------------------------------------------------------


class _StubSvc:
    def __init__(self):
        self.logs: list[str] = []
        self._http_responses: list[tuple[int, object, str]] = []

    def log(self, msg: str) -> None:
        self.logs.append(str(msg))

    @staticmethod
    def bool_cfg(cfg: dict, key: str, default: bool = False) -> bool:
        val = (cfg or {}).get(key, default)
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def to_int(value: object, fallback: int | None = None) -> int | None:
        if value is None:
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def coerce_list(value: object) -> list:
        if isinstance(value, list):
            return value
        if value is None:
            return []
        return [value]

    @staticmethod
    def parse_service_url(url: str, default_port: int) -> dict:
        host = url
        use_ssl = False
        base_url = ""
        if url.startswith("https://"):
            use_ssl = True
            host = url[len("https://"):]
        elif url.startswith("http://"):
            host = url[len("http://"):]
        port = default_port
        if ":" in host:
            host, _, port_str = host.partition(":")
            if "/" in port_str:
                port_str, _, base_url = port_str.partition("/")
                base_url = "/" + base_url
            try:
                port = int(port_str)
            except ValueError:
                port = default_port
        elif "/" in host:
            host, _, base_url = host.partition("/")
            base_url = "/" + base_url
        return {
            "hostname": host,
            "port": port,
            "use_ssl": use_ssl,
            "base_url": base_url,
        }

    @staticmethod
    def normalize_base_path(value: str) -> str:
        v = str(value or "").strip()
        if not v or v == "/":
            return ""
        return v

    @staticmethod
    def choose_profile(profiles: list, preferred_id=None, preferred_names=None) -> dict | None:
        if preferred_id is not None:
            for p in profiles:
                if p.get("id") == preferred_id:
                    return p
        if preferred_names:
            for name in preferred_names:
                for p in profiles:
                    if p.get("name") == name:
                        return p
        return profiles[0] if profiles else None

    @staticmethod
    def choose_root_folder(folders: list, preferred: str | None = None) -> str | None:
        if preferred:
            for f in folders:
                if f.get("path") == preferred:
                    return preferred
        return folders[0].get("path") if folders else None

    @staticmethod
    def find_existing_servarr(
        existing: list, name: str, hostname: str, port: int, base_url: str, is4k: bool
    ) -> dict | None:
        for entry in existing:
            if (
                entry.get("name") == name
                and entry.get("hostname") == hostname
                and entry.get("port") == port
            ):
                return entry
        return None

    @staticmethod
    def resolve_jellyfin_api_key(jellyfin_cfg: dict, config_root: str) -> str | None:
        return jellyfin_cfg.get("api_key")

    def http_request(self, base_url, path, *, api_key=None, method="GET", payload=None):
        if self._http_responses:
            return self._http_responses.pop(0)
        return (200, {}, "")

    def push_http(self, *responses: tuple[int, object, str]) -> None:
        self._http_responses.extend(responses)


# ---------------------------------------------------------------------------
# ensure_main_settings
# ---------------------------------------------------------------------------


class EnsureMainSettingsTests(unittest.TestCase):
    def test_already_correct_media_server_type(self):
        svc = _StubSvc()
        svc.push_http((200, {
            "mediaServerType": 2,
            "localLogin": True,
            "mediaServerLogin": False,
            "newPlexLogin": False,
        }, ""))
        ensure_main_settings(svc, "http://js:5055", "key", {})
        self.assertTrue(any("already correct" in m for m in svc.logs),
                        f"Expected 'already correct' log, got: {svc.logs}")

    def test_sets_media_server_type_when_different(self):
        svc = _StubSvc()
        svc.push_http(
            (200, {"mediaServerType": 1}, ""),
            (200, {}, ""),
        )
        ensure_main_settings(svc, "http://js:5055", "key", {})
        self.assertTrue(any("updated" in m for m in svc.logs),
                        f"Expected 'updated' log, got: {svc.logs}")

    def test_post_failure_raises(self):
        svc = _StubSvc()
        svc.push_http(
            (200, {"mediaServerType": 1}, ""),
            (500, None, "Server Error"),
        )
        with self.assertRaises(RuntimeError):
            ensure_main_settings(svc, "http://js:5055", "key", {})

    def test_get_failure_raises(self):
        svc = _StubSvc()
        svc.push_http((500, None, "Server Error"))
        with self.assertRaises(RuntimeError):
            ensure_main_settings(svc, "http://js:5055", "key", {})

    def test_explicit_media_server_type(self):
        svc = _StubSvc()
        svc.push_http(
            (200, {"mediaServerType": 1}, ""),
            (200, {}, ""),
        )
        ensure_main_settings(svc, "http://js:5055", "key", {"media_server_type": 3})
        self.assertTrue(any("updated" in m for m in svc.logs),
                        f"Expected 'updated' log, got: {svc.logs}")

    def test_no_op_when_media_server_type_none_and_default_disabled(self):
        svc = _StubSvc()
        cfg = {"set_media_server_type_jellyfin": False}
        ensure_main_settings(svc, "http://js:5055", "key", cfg)
        self.assertEqual(len(svc.logs), 0)

    def test_status_201_accepted(self):
        svc = _StubSvc()
        svc.push_http(
            (200, {"mediaServerType": 0}, ""),
            (201, {}, ""),
        )
        ensure_main_settings(svc, "http://js:5055", "key", {})
        self.assertTrue(any("updated" in m for m in svc.logs),
                        f"Expected 'updated' log, got: {svc.logs}")

    def test_status_202_accepted(self):
        svc = _StubSvc()
        svc.push_http(
            (200, {"mediaServerType": 0}, ""),
            (202, {}, ""),
        )
        ensure_main_settings(svc, "http://js:5055", "key", {})
        self.assertTrue(any("updated" in m for m in svc.logs),
                        f"Expected 'updated' log, got: {svc.logs}")

    def test_get_returns_non_dict_raises(self):
        svc = _StubSvc()
        svc.push_http((200, "not-a-dict", ""))
        with self.assertRaises(RuntimeError):
            ensure_main_settings(svc, "http://js:5055", "key", {})


# ---------------------------------------------------------------------------
# ensure_jellyfin_settings
# ---------------------------------------------------------------------------


class EnsureJellyfinSettingsTests(unittest.TestCase):
    def test_configure_disabled_noop(self):
        svc = _StubSvc()
        ensure_jellyfin_settings(svc, "http://js:5055", "key", {}, "/config")
        self.assertEqual(len(svc.logs), 0)

    def test_configure_success(self):
        svc = _StubSvc()
        svc.push_http((200, {}, ""))
        cfg = {"jellyfin": {"configure": True, "api_key": "jf-key", "url": "http://jellyfin:8096"}}
        ensure_jellyfin_settings(svc, "http://js:5055", "key", cfg, "/config")
        self.assertTrue(any("configured Jellyfin" in m for m in svc.logs))

    def test_missing_api_key_raises(self):
        svc = _StubSvc()
        cfg = {"jellyfin": {"configure": True}}
        with self.assertRaises(RuntimeError):
            ensure_jellyfin_settings(svc, "http://js:5055", "key", cfg, "/config")

    def test_post_failure_raises(self):
        svc = _StubSvc()
        svc.push_http((500, None, "Server Error"))
        cfg = {"jellyfin": {"configure": True, "api_key": "jf-key"}}
        with self.assertRaises(RuntimeError):
            ensure_jellyfin_settings(svc, "http://js:5055", "key", cfg, "/config")

    def test_default_jellyfin_url(self):
        svc = _StubSvc()
        svc.push_http((200, {}, ""))
        cfg = {"jellyfin": {"configure": True, "api_key": "jf-key"}}
        ensure_jellyfin_settings(svc, "http://js:5055", "key", cfg, "/config")
        self.assertTrue(any("configured Jellyfin" in m for m in svc.logs))

    def test_status_201_accepted(self):
        svc = _StubSvc()
        svc.push_http((201, {}, ""))
        cfg = {"jellyfin": {"configure": True, "api_key": "jf-key"}}
        ensure_jellyfin_settings(svc, "http://js:5055", "key", cfg, "/config")
        self.assertTrue(any("configured Jellyfin" in m for m in svc.logs))


# ---------------------------------------------------------------------------
# ensure_radarr
# ---------------------------------------------------------------------------

_RADARR_TEST_RESPONSE = {
    "profiles": [{"id": 1, "name": "HD-1080p"}, {"id": 2, "name": "Ultra-HD"}],
    "rootFolders": [{"path": "/movies"}],
    "urlBase": "",
}

_RADARR_APP_CFG = {"url": "http://radarr:7878", "name": "Radarr", "root_folder": "/movies"}


class EnsureRadarrTests(unittest.TestCase):
    def test_disabled_noop(self):
        svc = _StubSvc()
        ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {"radarr": {"enabled": False}})
        self.assertEqual(len(svc.logs), 0)

    def test_create_new_mapping(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_RADARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (200, {}, ""),
        )
        ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})
        self.assertTrue(any("created Radarr" in m for m in svc.logs))

    def test_update_existing_mapping(self):
        svc = _StubSvc()
        existing = [{"id": 10, "name": "Radarr", "hostname": "radarr", "port": 7878}]
        svc.push_http(
            (200, dict(_RADARR_TEST_RESPONSE), ""),
            (200, existing, ""),
            (200, {}, ""),
        )
        ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})
        self.assertTrue(any("updated Radarr" in m for m in svc.logs))

    def test_existing_without_id_legacy(self):
        svc = _StubSvc()
        existing = [{"name": "Radarr", "hostname": "radarr", "port": 7878}]
        svc.push_http(
            (200, dict(_RADARR_TEST_RESPONSE), ""),
            (200, existing, ""),
        )
        ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})
        self.assertTrue(any("legacy" in m for m in svc.logs))

    def test_test_failure_raises(self):
        svc = _StubSvc()
        svc.push_http((500, None, "Server Error"))
        with self.assertRaises(RuntimeError):
            ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})

    def test_no_profiles_raises(self):
        svc = _StubSvc()
        svc.push_http((200, {"profiles": [], "rootFolders": [{"path": "/m"}]}, ""))
        with self.assertRaises(RuntimeError):
            ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})

    def test_no_root_folders_raises(self):
        svc = _StubSvc()
        svc.push_http(
            (200, {"profiles": [{"id": 1, "name": "HD"}], "rootFolders": []}, ""),
        )
        with self.assertRaises(RuntimeError):
            ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})

    def test_list_failure_raises(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_RADARR_TEST_RESPONSE), ""),
            (500, None, "Server Error"),
        )
        with self.assertRaises(RuntimeError):
            ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})

    def test_create_failure_raises(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_RADARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (500, None, "Server Error"),
        )
        with self.assertRaises(RuntimeError):
            ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})

    def test_update_failure_raises(self):
        svc = _StubSvc()
        existing = [{"id": 10, "name": "Radarr", "hostname": "radarr", "port": 7878}]
        svc.push_http(
            (200, dict(_RADARR_TEST_RESPONSE), ""),
            (200, existing, ""),
            (500, None, "Server Error"),
        )
        with self.assertRaises(RuntimeError):
            ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", {})

    def test_preferred_profile_by_name(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_RADARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (200, {}, ""),
        )
        cfg = {"radarr": {"quality_profile_preferred_names": ["Ultra-HD"]}}
        ensure_radarr(svc, "http://js:5055", "key", _RADARR_APP_CFG, "radarr-key", cfg)
        self.assertTrue(any("created Radarr" in m for m in svc.logs))


# ---------------------------------------------------------------------------
# ensure_sonarr
# ---------------------------------------------------------------------------

_SONARR_TEST_RESPONSE = {
    "profiles": [{"id": 1, "name": "HD-1080p"}],
    "rootFolders": [{"path": "/tv"}],
    "languageProfiles": [{"id": 1, "name": "English"}],
    "urlBase": "",
}

_SONARR_APP_CFG = {"url": "http://sonarr:8989", "name": "Sonarr", "root_folder": "/tv"}


class EnsureSonarrTests(unittest.TestCase):
    def test_disabled_noop(self):
        svc = _StubSvc()
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {"sonarr": {"enabled": False}})
        self.assertEqual(len(svc.logs), 0)

    def test_create_new_mapping(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (200, {}, ""),
        )
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})
        self.assertTrue(any("created Sonarr" in m for m in svc.logs))

    def test_update_existing_mapping(self):
        svc = _StubSvc()
        existing = [{"id": 5, "name": "Sonarr", "hostname": "sonarr", "port": 8989}]
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, existing, ""),
            (200, {}, ""),
        )
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})
        self.assertTrue(any("updated Sonarr" in m for m in svc.logs))

    def test_existing_without_id_legacy(self):
        svc = _StubSvc()
        existing = [{"name": "Sonarr", "hostname": "sonarr", "port": 8989}]
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, existing, ""),
        )
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})
        self.assertTrue(any("legacy" in m for m in svc.logs))

    def test_test_failure_raises(self):
        svc = _StubSvc()
        svc.push_http((500, None, "fail"))
        with self.assertRaises(RuntimeError):
            ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})

    def test_no_profiles_raises(self):
        svc = _StubSvc()
        resp = dict(_SONARR_TEST_RESPONSE)
        resp["profiles"] = []
        svc.push_http((200, resp, ""))
        with self.assertRaises(RuntimeError):
            ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})

    def test_no_root_folders_raises(self):
        svc = _StubSvc()
        resp = dict(_SONARR_TEST_RESPONSE)
        resp["rootFolders"] = []
        svc.push_http((200, resp, ""))
        with self.assertRaises(RuntimeError):
            ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})

    def test_list_failure_raises(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (500, None, "fail"),
        )
        with self.assertRaises(RuntimeError):
            ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})

    def test_create_failure_raises(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (500, None, "fail"),
        )
        with self.assertRaises(RuntimeError):
            ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})

    def test_update_failure_raises(self):
        svc = _StubSvc()
        existing = [{"id": 5, "name": "Sonarr", "hostname": "sonarr", "port": 8989}]
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, existing, ""),
            (500, None, "fail"),
        )
        with self.assertRaises(RuntimeError):
            ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})

    def test_invalid_series_type_defaults_to_standard(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (200, {}, ""),
        )
        cfg = {"sonarr": {"series_type": "bogus"}}
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", cfg)
        self.assertTrue(any("created Sonarr" in m for m in svc.logs))

    def test_invalid_anime_series_type_defaults_to_anime(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (200, {}, ""),
        )
        cfg = {"sonarr": {"anime_series_type": "bogus"}}
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", cfg)
        self.assertTrue(any("created Sonarr" in m for m in svc.logs))

    def test_invalid_monitor_new_items_defaults_to_all(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (200, {}, ""),
        )
        cfg = {"sonarr": {"monitor_new_items": "bogus"}}
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", cfg)
        self.assertTrue(any("created Sonarr" in m for m in svc.logs))

    def test_status_201_accepted(self):
        svc = _StubSvc()
        svc.push_http(
            (200, dict(_SONARR_TEST_RESPONSE), ""),
            (200, [], ""),
            (201, {}, ""),
        )
        ensure_sonarr(svc, "http://js:5055", "key", _SONARR_APP_CFG, "sonarr-key", {})
        self.assertTrue(any("created Sonarr" in m for m in svc.logs))


if __name__ == "__main__":
    unittest.main()
