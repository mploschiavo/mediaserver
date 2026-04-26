import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.auth_service import AuthService  # noqa: E402


class AuthServiceTests(unittest.TestCase):
    def _service(self, http_request):
        return AuthService(
            http_request=http_request,
            log=lambda _msg: None,
            bool_cfg=lambda cfg, key, default=False: bool((cfg or {}).get(key, default)),
        )

    def test_applies_path_prefix_url_base_for_all_arr_and_prowlarr_apps(self):
        put_payloads: list[dict[str, object]] = []

        def http_request(base_url, path, api_key=None, method="GET", payload=None):
            del base_url, api_key
            if method == "GET" and path == "/api/v3/config/host":
                return (
                    200,
                    {
                        "authenticationMethod": "None",
                        "authenticationRequired": "DisabledForLocalAddresses",
                        "username": "",
                        "urlBase": "",
                    },
                    "{}",
                )
            if method == "PUT" and path == "/api/v3/config/host":
                put_payloads.append(dict(payload or {}))
                return 202, {}, "{}"
            raise AssertionError(f"Unexpected request: method={method} path={path}")

        auth_cfg = {
            "enabled": True,
            "method": "Forms",
            "required": "Enabled",
            "username_env": "STACK_ADMIN_USERNAME",
            "password_env": "STACK_ADMIN_PASSWORD",
            "path_prefix_url_base_by_app": {
                "sonarr": "/app/sonarr",
                "radarr": "/app/radarr",
                "lidarr": "/app/lidarr",
                "readarr": "/app/readarr",
                "prowlarr": "/app/prowlarr",
            },
        }
        apps = [
            ("Sonarr", "Sonarr", "/app/sonarr"),
            ("Radarr", "Radarr", "/app/radarr"),
            ("Lidarr", "Lidarr", "/app/lidarr"),
            ("Readarr", "Readarr", "/app/readarr"),
            ("Prowlarr", "Prowlarr", "/app/prowlarr"),
        ]

        with mock.patch.dict(
            os.environ,
            {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "media-dev"},
            clear=False,
        ):
            svc = self._service(http_request)
            for app_name, implementation, expected_url_base in apps:
                svc.ensure_app_auth_settings(
                    app_name=app_name,
                    implementation=implementation,
                    app_url=f"http://{implementation.lower()}:8989",
                    api_base="/api/v3",
                    api_key="test-api-key",
                    auth_cfg=auth_cfg,
                )

        self.assertEqual(len(put_payloads), len(apps))
        for payload, (_, _, expected_url_base) in zip(put_payloads, apps):
            self.assertEqual(payload.get("urlBase"), expected_url_base)
            self.assertEqual(payload.get("UrlBase"), expected_url_base)
            self.assertEqual(payload.get("authenticationMethod"), "Forms")
            self.assertEqual(payload.get("authenticationRequired"), "Enabled")
            self.assertEqual(payload.get("username"), "admin")

    def test_does_not_set_url_base_when_no_mapping_exists(self):
        put_payloads: list[dict[str, object]] = []

        def http_request(base_url, path, api_key=None, method="GET", payload=None):
            del base_url, api_key
            if method == "GET" and path == "/api/v1/config/host":
                return (
                    200,
                    {
                        "authenticationMethod": "None",
                        "authenticationRequired": "DisabledForLocalAddresses",
                        "username": "",
                        "urlBase": "",
                    },
                    "{}",
                )
            if method == "PUT" and path == "/api/v1/config/host":
                put_payloads.append(dict(payload or {}))
                return 202, {}, "{}"
            raise AssertionError(f"Unexpected request: method={method} path={path}")

        auth_cfg = {
            "enabled": True,
            "method": "Forms",
            "required": "Enabled",
            "username_env": "STACK_ADMIN_USERNAME",
            "password_env": "STACK_ADMIN_PASSWORD",
        }
        with mock.patch.dict(
            os.environ,
            {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "media-dev"},
            clear=False,
        ):
            svc = self._service(http_request)
            svc.ensure_app_auth_settings(
                app_name="Sonarr",
                implementation="Sonarr",
                app_url="http://sonarr:8989",
                api_base="/api/v1",
                api_key="test-api-key",
                auth_cfg=auth_cfg,
            )

        self.assertEqual(len(put_payloads), 1)
        self.assertEqual(put_payloads[0].get("urlBase"), "")
        self.assertFalse("UrlBase" in put_payloads[0])


if __name__ == "__main__":
    unittest.main()
