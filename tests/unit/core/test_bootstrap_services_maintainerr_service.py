import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.maintainerr.service import MaintainerrService  # noqa: E402


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

    def test_ensure_integrations_uses_path_aware_maintainerr_url_when_configured(self):
        http_request = mock.Mock()
        service = self._service(http_request)
        service.ensure_integrations(
            cfg={
                "app_auth": {
                    "path_prefix_url_base_by_app": {
                        "maintainerr": "/app/maintainerr",
                    }
                },
                "maintainerr": {
                    "enabled": True,
                    "url": "http://maintainerr:6246",
                    "integrations": {
                        "enabled": True,
                        "sync_rules": False,
                        "main": {"enabled": False},
                        "radarr": {"enabled": False},
                        "sonarr": {"enabled": False},
                        "jellyseerr": {"enabled": False},
                        "tautulli": {"enabled": False},
                    },
                },
            },
            config_root="/srv-config",
            arr_apps=[],
            wait_timeout=10,
        )

        service.wait_for_service.assert_called_once_with(
            "Maintainerr",
            "http://maintainerr:6246/app/maintainerr",
            "/api/settings",
            10,
        )
        http_request.assert_not_called()

    def test_ensure_integrations_skips_radarr_when_arr_app_missing_and_not_required(self):
        http_request = mock.Mock()
        service = self._service(http_request)
        service.ensure_integrations(
            cfg={
                "maintainerr": {
                    "enabled": True,
                    "url": "http://maintainerr:6246",
                    "integrations": {
                        "enabled": True,
                        "sync_rules": False,
                        "main": {"enabled": False},
                        "radarr": {"enabled": True, "required": False},
                        "sonarr": {"enabled": False},
                        "jellyseerr": {"enabled": False},
                        "tautulli": {"enabled": False},
                    },
                }
            },
            config_root="/srv-config",
            arr_apps=[],
            wait_timeout=10,
        )

        http_request.assert_not_called()
        self.assertTrue(
            any(
                "skipping radarr integration" in str(call.args[0]).lower()
                for call in service.log.call_args_list
                if call.args
            )
        )

    def test_ensure_integrations_fails_when_required_radarr_arr_app_is_missing(self):
        http_request = mock.Mock()
        service = self._service(http_request)
        with self.assertRaisesRegex(RuntimeError, "Radarr integration is enabled and required"):
            service.ensure_integrations(
                cfg={
                    "maintainerr": {
                        "enabled": True,
                        "url": "http://maintainerr:6246",
                        "integrations": {
                            "enabled": True,
                            "sync_rules": False,
                            "main": {"enabled": False},
                            "radarr": {"enabled": True, "required": True},
                            "sonarr": {"enabled": False},
                            "jellyseerr": {"enabled": False},
                            "tautulli": {"enabled": False},
                        },
                    }
                },
                config_root="/srv-config",
                arr_apps=[],
                wait_timeout=10,
            )

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
                        "integrations": {
                            "enabled": True,
                            "test_connections": True,
                            "sync_rules": False,
                        },
                    },
                    "jellyseerr": {"url": "http://jellyseerr:5055"},
                    "tautulli": {"url": "http://tautulli:8181"},
                },
                config_root="/srv-config",
                arr_apps=[
                    {
                        "implementation": "Radarr",
                        "name": "Radarr",
                        "url": "http://radarr:7878",
                    },
                    {
                        "implementation": "Sonarr",
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

    def test_ensure_integrations_skips_optional_tautulli_when_key_missing(self):
        calls: list[tuple[str, str, dict | None]] = []

        def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=30):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if (method, path) == ("GET", "/api/settings"):
                return 200, {}, "{}"
            if method == "POST":
                return 200, {"ok": True}, "{}"
            raise AssertionError(f"unexpected request: {method} {path}")

        service = self._service(http_request)
        service.ensure_integrations(
            cfg={
                "maintainerr": {
                    "enabled": True,
                    "url": "http://maintainerr:6246",
                    "integrations": {
                        "enabled": True,
                        "test_connections": False,
                        "sync_rules": False,
                        "radarr": {"enabled": False},
                        "sonarr": {"enabled": False},
                        "jellyseerr": {"enabled": False},
                        "tautulli": {
                            "enabled": True,
                            "required": False,
                            "api_key_env": "MISSING_TAUTULLI_KEY",
                            "api_key_config_path": "tautulli/missing.ini",
                        },
                    },
                }
            },
            config_root="/srv-config",
            arr_apps=[],
            wait_timeout=10,
        )

        posted_paths = [path for method, path, _payload in calls if method == "POST"]
        self.assertIn("/api/settings", posted_paths)
        self.assertNotIn("/api/settings/tautulli", posted_paths)
        self.assertTrue(
            any(
                "optional tautulli" in str(call.args[0]).lower()
                for call in service.log.call_args_list
                if call.args
            )
        )

    def test_ensure_integrations_is_idempotent_when_already_configured(self):
        calls: list[tuple[str, str, dict | None]] = []

        def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=30):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if (method, path) == ("GET", "/api/settings"):
                return (
                    200,
                    {
                        "applicationUrl": "maintainerr.local",
                        "media_server_type": "jellyfin",
                        "seerr_url": "http://jellyseerr:5055",
                        "seerr_api_key": "jellyseerr-key",
                        "jellyfin_url": "http://jellyfin:8096",
                        "jellyfin_server_name": "Jellyfin",
                        "tautulli_url": "http://tautulli:8181",
                        "tautulli_api_key": "tautulli-key",
                    },
                    "{}",
                )
            if (method, path) == ("GET", "/api/settings/radarr"):
                return (
                    200,
                    [
                        {
                            "serverName": "Radarr",
                            "url": "http://radarr:7878",
                            "apiKey": "radarr-key",
                        }
                    ],
                    "[]",
                )
            if (method, path) == ("GET", "/api/settings/sonarr"):
                return (
                    200,
                    [
                        {
                            "serverName": "Sonarr",
                            "url": "http://sonarr:8989",
                            "apiKey": "sonarr-key",
                        }
                    ],
                    "[]",
                )
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
                        "integrations": {
                            "enabled": True,
                            "test_connections": False,
                            "sync_rules": False,
                        },
                    },
                    "jellyseerr": {"url": "http://jellyseerr:5055"},
                    "tautulli": {"url": "http://tautulli:8181"},
                },
                config_root="/srv-config",
                arr_apps=[
                    {
                        "implementation": "Radarr",
                        "name": "Radarr",
                        "url": "http://radarr:7878",
                    },
                    {
                        "implementation": "Sonarr",
                        "name": "Sonarr",
                        "url": "http://sonarr:8989",
                    },
                ],
                wait_timeout=30,
            )

        posted_paths = [path for method, path, _payload in calls if method == "POST"]
        self.assertEqual([], posted_paths)

    def test_ensure_integrations_syncs_policy_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_root = Path(tmp)
            (cfg_root / "maintainerr").mkdir(parents=True, exist_ok=True)
            policy = {
                "version": 1,
                "rules": [
                    {
                        "name": "Delete Watched Movies After 30 Days",
                        "description": "Delete watched movies.",
                        "libraries": ["Movies"],
                        "conditions": {"watched": True, "added_days_ago_gte": 30},
                        "actions": {"delete_item": True},
                    },
                    {
                        "name": "Delete Watched TV After 30 Days",
                        "description": "Delete watched TV.",
                        "libraries": ["TV Shows"],
                        "conditions": {"watched": True, "added_days_ago_gte": 30},
                        "actions": {"delete_item": True},
                    },
                    {
                        "name": "Delete Played Music After 30 Days",
                        "description": "Unsupported library should be skipped.",
                        "libraries": ["Music"],
                        "conditions": {"watched": True, "added_days_ago_gte": 30},
                        "actions": {"delete_item": True},
                    },
                ],
            }
            (cfg_root / "maintainerr" / "policy.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )

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
                if (method, path) == ("GET", "/api/media-server/libraries"):
                    return (
                        200,
                        [
                            {"id": "lib-movies", "title": "Movies", "type": "movie"},
                            {"id": "lib-tv", "title": "TV Shows", "type": "show"},
                        ],
                        "[]",
                    )
                if (method, path) == ("GET", "/api/rules?activeOnly=false"):
                    return 200, [], "[]"
                if (method, path) == ("POST", "/api/rules"):
                    return 201, {"code": 1, "result": "Success"}, "{}"
                if method == "POST":
                    return 200, {"ok": True}, "{}"
                raise AssertionError(f"unexpected request: {method} {path}")

            with mock.patch.dict(
                "os.environ",
                {
                    "TAUTULLI_API_KEY": "tautulli-key",
                    "JELLYFIN_API_KEY": "jf-key",
                    "JELLYFIN_USER_ID": "jf-user",
                },
                clear=False,
            ):
                service = self._service(http_request)
                service.ensure_integrations(
                    cfg={
                        "maintainerr": {
                            "enabled": True,
                            "url": "http://maintainerr:6246",
                            "policy_relative_path": "maintainerr/policy.json",
                            "integrations": {
                                "enabled": True,
                                "test_connections": False,
                                "sync_rules": True,
                            },
                        },
                        "jellyseerr": {"url": "http://jellyseerr:5055"},
                        "tautulli": {"url": "http://tautulli:8181"},
                    },
                    config_root=str(cfg_root),
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

            rule_posts = [
                payload
                for method, path, payload in calls
                if method == "POST" and path == "/api/rules"
            ]
            self.assertEqual(2, len(rule_posts))
            names = {payload.get("name") for payload in rule_posts if isinstance(payload, dict)}
            self.assertIn("Delete Watched Movies After 30 Days", names)
            self.assertIn("Delete Watched TV After 30 Days", names)
            for payload in rule_posts:
                self.assertEqual([], payload.get("notifications"))
                self.assertTrue(payload.get("rules"))

    def test_ensure_integrations_accepts_native_rule_payload_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_root = Path(tmp)
            (cfg_root / "maintainerr").mkdir(parents=True, exist_ok=True)
            policy = {
                "version": 1,
                "rules": [
                    {
                        "name": "Native Maintainerr Rule",
                        "description": "API-native shape",
                        "libraryTitles": ["Movies"],
                        "dataType": "movie",
                        "arrAction": 0,
                        "useRules": True,
                        "rules": [
                            {
                                "ruleJson": '{"firstVal":[6,0],"operator":null,'
                                '"action":5,"customVal":{"ruleTypeId":1,'
                                '"value":"days_ago:30"},"section":0}'
                            }
                        ],
                        "collection": {
                            "visibleOnHome": False,
                            "visibleOnRecommended": False,
                            "keepLogsForMonths": 6,
                        },
                    }
                ],
            }
            (cfg_root / "maintainerr" / "policy.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )

            posted_rules: list[dict] = []

            def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=30):
                del base_url, api_key, timeout
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
                if (method, path) == ("GET", "/api/media-server/libraries"):
                    return (
                        200,
                        [
                            {"id": "lib-movies", "title": "Movies", "type": "movie"},
                        ],
                        "[]",
                    )
                if (method, path) == ("GET", "/api/rules?activeOnly=false"):
                    return 200, [], "[]"
                if (method, path) == ("POST", "/api/rules"):
                    posted_rules.append(payload)
                    return 201, {"code": 1, "result": "Success"}, "{}"
                if method == "POST":
                    return 200, {"ok": True}, "{}"
                raise AssertionError(f"unexpected request: {method} {path}")

            with mock.patch.dict(
                "os.environ",
                {
                    "TAUTULLI_API_KEY": "tautulli-key",
                    "JELLYFIN_API_KEY": "jf-key",
                    "JELLYFIN_USER_ID": "jf-user",
                },
                clear=False,
            ):
                service = self._service(http_request)
                service.ensure_integrations(
                    cfg={
                        "maintainerr": {
                            "enabled": True,
                            "url": "http://maintainerr:6246",
                            "policy_relative_path": "maintainerr/policy.json",
                            "integrations": {
                                "enabled": True,
                                "test_connections": False,
                                "sync_rules": True,
                            },
                        },
                        "jellyseerr": {"url": "http://jellyseerr:5055"},
                        "tautulli": {"url": "http://tautulli:8181"},
                    },
                    config_root=str(cfg_root),
                    arr_apps=[],
                    wait_timeout=30,
                )

            self.assertEqual(1, len(posted_rules))
            payload = posted_rules[0]
            self.assertEqual("Native Maintainerr Rule", payload["name"])
            self.assertEqual("movie", payload["dataType"])
            self.assertEqual(0, payload["arrAction"])
            self.assertEqual(6, payload["collection"]["keepLogsForMonths"])
            self.assertEqual([6, 0], payload["rules"][0]["firstVal"])
            val = str((payload["rules"][0].get("customVal") or {}).get("value") or "")
            self.assertRegex(val, re.compile(r"^\d{4}-\d{2}-\d{2}T"))

    def test_ensure_integrations_decodes_maintainerr_yaml_rule_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg_root = Path(tmp)
            (cfg_root / "maintainerr").mkdir(parents=True, exist_ok=True)
            policy = {
                "version": 1,
                "rules": [
                    {
                        "name": "YAML Export Rule",
                        "mediaType": "MOVIES",
                        "rules": [
                            {
                                "0": [
                                    {
                                        "firstValue": "Jellyfin.viewCount",
                                        "action": "BIGGER",
                                        "customValue": {"type": "number", "value": 0},
                                    }
                                ]
                            }
                        ],
                    }
                ],
            }
            (cfg_root / "maintainerr" / "policy.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )

            posted_rules: list[dict] = []

            def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=30):
                del base_url, api_key, timeout
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
                if (method, path) == ("GET", "/api/media-server/libraries"):
                    return (
                        200,
                        [
                            {"id": "lib-movies", "title": "Movies", "type": "movie"},
                            {"id": "lib-tv", "title": "TV Shows", "type": "show"},
                        ],
                        "[]",
                    )
                if (method, path) == ("POST", "/api/rules/yaml/decode"):
                    result = {
                        "mediaType": "movie",
                        "rules": [
                            {
                                "firstVal": [6, 5],
                                "operator": None,
                                "action": 0,
                                "customVal": {"ruleTypeId": 0, "value": "0"},
                                "section": 0,
                            }
                        ],
                    }
                    return 200, {"code": 1, "result": json.dumps(result)}, "{}"
                if (method, path) == ("GET", "/api/rules?activeOnly=false"):
                    return 200, [], "[]"
                if (method, path) == ("POST", "/api/rules"):
                    posted_rules.append(payload)
                    return 201, {"code": 1, "result": "Success"}, "{}"
                if method == "POST":
                    return 200, {"ok": True}, "{}"
                raise AssertionError(f"unexpected request: {method} {path}")

            with mock.patch.dict(
                "os.environ",
                {
                    "TAUTULLI_API_KEY": "tautulli-key",
                    "JELLYFIN_API_KEY": "jf-key",
                    "JELLYFIN_USER_ID": "jf-user",
                },
                clear=False,
            ):
                service = self._service(http_request)
                service.ensure_integrations(
                    cfg={
                        "maintainerr": {
                            "enabled": True,
                            "url": "http://maintainerr:6246",
                            "policy_relative_path": "maintainerr/policy.json",
                            "integrations": {
                                "enabled": True,
                                "test_connections": False,
                                "sync_rules": True,
                            },
                        },
                        "jellyseerr": {"url": "http://jellyseerr:5055"},
                        "tautulli": {"url": "http://tautulli:8181"},
                    },
                    config_root=str(cfg_root),
                    arr_apps=[],
                    wait_timeout=30,
                )

            self.assertEqual(1, len(posted_rules))
            payload = posted_rules[0]
            self.assertEqual("YAML Export Rule", payload["name"])
            self.assertEqual("movie", payload["dataType"])
            self.assertEqual("lib-movies", payload["libraryId"])


if __name__ == "__main__":
    unittest.main()
