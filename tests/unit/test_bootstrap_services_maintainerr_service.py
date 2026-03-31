import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.maintainerr_service import MaintainerrService  # noqa: E402


class MaintainerrServiceTests(unittest.TestCase):
    def _service(self, http_request, *, resolve_path=None):
        return MaintainerrService(
            log=mock.Mock(),
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
            normalize_url=lambda value: str(value or "").rstrip("/"),
            wait_for_service=mock.Mock(),
            http_request=http_request,
            read_api_key=mock.Mock(side_effect=lambda _root, app: f"{app}-key"),
            read_jellyseerr_api_key=mock.Mock(return_value="jellyseerr-key"),
            get_arr_app=lambda apps, impl: next(
                (app for app in (apps or []) if app.get("implementation") == impl),
                None,
            ),
            resolve_path=resolve_path or (lambda root, rel: Path(root) / rel),
        )

    def test_ensure_integrations_skips_when_maintainerr_disabled(self):
        http_request = mock.Mock()
        service = self._service(http_request)
        service.ensure_integrations(
            cfg={"maintainerr": {"enabled": False}},
            config_root="/srv-config",
            arr_apps=[],
            wait_timeout=10,
        )
        http_request.assert_not_called()

    def test_ensure_integrations_configures_and_tests_all_targets(self):
        calls: list[tuple[str, str, dict | None]] = []

        def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=30):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if (method, path) == ("GET", "/api/settings"):
                return 200, {}, "{}"
            if (method, path) == ("GET", "/api/settings/radarr"):
                return 200, [], "[]"
            if (method, path) == ("GET", "/api/settings/sonarr"):
                return 200, [], "[]"
            if (method, path) == ("GET", "/api/settings/seerr"):
                return 200, {"url": "", "api_key": ""}, "{}"
            if (method, path) == ("GET", "/api/settings/tautulli"):
                return 200, {"url": "", "api_key": ""}, "{}"
            if method == "POST":
                return 200, {"ok": True}, "{}"
            raise AssertionError(f"unexpected request: {method} {path}")

        with mock.patch.dict(
            "os.environ",
            {
                "TAUTULLI_API_KEY": "tautulli-key",
            },
            clear=False,
        ):
            service = self._service(http_request)
            service.ensure_integrations(
                cfg={
                    "maintainerr": {
                        "enabled": True,
                        "url": "http://maintainerr:6246",
                        "integrations": {"enabled": True, "test_connections": True},
                    },
                    "jellyseerr": {"url": "http://jellyseerr:5055"},
                    "tautulli": {"url": "http://tautulli:8181"},
                },
                config_root="/srv-config",
                arr_apps=[
                    {
                        "implementation": "radarr",
                        "name": "Radarr",
                        "url": "http://radarr:7878",
                    },
                    {
                        "implementation": "sonarr",
                        "name": "Sonarr",
                        "url": "http://sonarr:8989",
                    },
                ],
                wait_timeout=30,
            )

        posted_paths = [path for method, path, _payload in calls if method == "POST"]
        self.assertIn("/api/settings", posted_paths)
        self.assertIn("/api/settings/radarr", posted_paths)
        self.assertIn("/api/settings/sonarr", posted_paths)
        self.assertIn("/api/settings/seerr", posted_paths)
        self.assertIn("/api/settings/tautulli", posted_paths)
        self.assertIn("/api/settings/test/radarr", posted_paths)
        self.assertIn("/api/settings/test/sonarr", posted_paths)
        self.assertIn("/api/settings/test/seerr", posted_paths)
        self.assertIn("/api/settings/test/tautulli", posted_paths)

    def test_ensure_integrations_is_idempotent_when_already_configured(self):
        calls: list[tuple[str, str, dict | None]] = []

        def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=30):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if (method, path) == ("GET", "/api/settings"):
                return 200, {
                    "applicationUrl": "maintainerr.local",
                    "media_server_type": "jellyfin",
                    "seerr_url": "http://jellyseerr:5055",
                    "seerr_api_key": "jellyseerr-key",
                    "jellyfin_url": "http://jellyfin:8096",
                    "jellyfin_server_name": "Jellyfin",
                    "tautulli_url": "http://tautulli:8181",
                    "tautulli_api_key": "tautulli-key",
                }, "{}"
            if (method, path) == ("GET", "/api/settings/radarr"):
                return 200, [
                    {
                        "serverName": "Radarr",
                        "url": "http://radarr:7878",
                        "apiKey": "radarr-key",
                    }
                ], "[]"
            if (method, path) == ("GET", "/api/settings/sonarr"):
                return 200, [
                    {
                        "serverName": "Sonarr",
                        "url": "http://sonarr:8989",
                        "apiKey": "sonarr-key",
                    }
                ], "[]"
            if (method, path) == ("GET", "/api/settings/seerr"):
                return 200, {"url": "http://jellyseerr:5055", "api_key": "jellyseerr-key"}, "{}"
            if (method, path) == ("GET", "/api/settings/tautulli"):
                return 200, {"url": "http://tautulli:8181", "api_key": "tautulli-key"}, "{}"
            if method == "POST":
                return 200, {"ok": True}, "{}"
            raise AssertionError(f"unexpected request: {method} {path}")

        with mock.patch.dict(
            "os.environ",
            {
                "TAUTULLI_API_KEY": "tautulli-key",
            },
            clear=False,
        ):
            service = self._service(http_request)
            service.ensure_integrations(
                cfg={
                    "maintainerr": {
                        "enabled": True,
                        "url": "http://maintainerr:6246",
                        "integrations": {"enabled": True, "test_connections": False},
                    },
                    "jellyseerr": {"url": "http://jellyseerr:5055"},
                    "tautulli": {"url": "http://tautulli:8181"},
                },
                config_root="/srv-config",
                arr_apps=[
                    {
                        "implementation": "radarr",
                        "name": "Radarr",
                        "url": "http://radarr:7878",
                    },
                    {
                        "implementation": "sonarr",
                        "name": "Sonarr",
                        "url": "http://sonarr:8989",
                    },
                ],
                wait_timeout=30,
            )

        posted_paths = [path for method, path, _payload in calls if method == "POST"]
        self.assertEqual([], posted_paths)


if __name__ == "__main__":
    unittest.main()
