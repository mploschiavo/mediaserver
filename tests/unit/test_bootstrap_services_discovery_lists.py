import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.discovery_lists_service import DiscoveryListsService  # noqa: E402


class DiscoveryListsServiceTests(unittest.TestCase):
    def _svc(self, http_request, logs):
        return DiscoveryListsService(
            bool_cfg=lambda cfg, key, fallback: bool(cfg.get(key, fallback)),
            coerce_list=lambda value: value if isinstance(value, list) else ([] if value is None else [value]),
            log=logs.append,
            http_request=http_request,
            resolve_env_placeholder=lambda value: value,
            field_map=lambda fields: {str(f.get("name")): f.get("value") for f in (fields or []) if isinstance(f, dict)},
            field_list=lambda values: [{"name": key, "value": value} for key, value in values.items()],
            to_int=lambda value, fallback=None: int(value) if str(value).strip().isdigit() else fallback,
            normalize_token=lambda value: str(value or "").strip().lower(),
            resolve_arr_quality_preferences=lambda _cfg, _app_cfg: (None, []),
            get_arr_quality_profile=lambda *_args, **_kwargs: {"id": 4, "name": "HD-1080p"},
            pick_first_profile_id=lambda *_args, **_kwargs: 1,
            env_truthy=lambda _name, default=False: default,
            trigger_arr_command=lambda *_args, **_kwargs: True,
        )

    def test_sonarr_seed_series_adds_entries_when_import_lists_empty(self):
        calls = []

        def http_request(_app_url, path, api_key=None, method="GET", payload=None):
            calls.append((method, path, payload))
            self.assertEqual(api_key, "sonarr-api")
            if method == "GET" and path == "/api/v3/series":
                return 200, [], ""
            if method == "GET" and path == "/api/v3/languageprofile":
                return 200, [{"id": 1}], ""
            if method == "GET" and path.startswith("/api/v3/series/lookup?term="):
                return 200, [{"title": "Breaking Bad", "tvdbId": 81189, "titleSlug": "breaking-bad"}], ""
            if method == "POST" and path == "/api/v3/series":
                return 201, {"id": 1}, ""
            return 200, [], ""

        logs = []
        svc = self._svc(http_request=http_request, logs=logs)
        cfg = {
            "arr_discovery_lists": {
                "enabled": True,
                "Sonarr": [],
            },
            "sonarr_seed_series": {
                "enabled": True,
                "max_series": 1,
                "series": ["Breaking Bad"],
            },
        }
        app_cfg = {
            "name": "Sonarr",
            "implementation": "Sonarr",
            "root_folder": "/media/tv",
        }

        svc.ensure_arr_discovery_lists_for_app(
            cfg=cfg,
            app_cfg=app_cfg,
            app_url="http://sonarr:8989",
            api_base="/api/v3",
            api_key="sonarr-api",
        )

        post_calls = [entry for entry in calls if entry[0] == "POST" and entry[1] == "/api/v3/series"]
        self.assertEqual(len(post_calls), 1)
        payload = post_calls[0][2] or {}
        self.assertEqual(payload.get("tvdbId"), 81189)
        self.assertEqual(payload.get("rootFolderPath"), "/media/tv")
        self.assertEqual(payload.get("qualityProfileId"), 4)
        self.assertTrue(any("seed series reconcile complete" in line for line in logs))


if __name__ == "__main__":
    unittest.main()

