import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.apps.servarr.policy_service import ServarrPolicyService  # noqa: E402


def _bool_cfg(cfg, key, default):
    return bool((cfg or {}).get(key, default))


def _coerce_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_token(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _to_int(value, fallback=None):
    try:
        if value is None:
            return fallback
        return int(value)
    except (TypeError, ValueError):
        return fallback


class ServarrPolicyServiceTests(unittest.TestCase):
    def _service(self, http_request, quality_profile=None):
        return ServarrPolicyService(
            http_request=http_request,
            bool_cfg=_bool_cfg,
            coerce_list=_coerce_list,
            normalize_token=_normalize_token,
            to_int=_to_int,
            resolve_arr_quality_preferences=lambda _cfg, _app: (None, []),
            get_arr_quality_profile=lambda *_args, **_kwargs: quality_profile or {},
            log=lambda _msg: None,
        )

    def test_fetch_download_client_config_uses_legacy_fallback(self):
        calls = []

        def fake_http(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, method, payload, timeout
            calls.append(path)
            if path.endswith("/config/downloadclient"):
                return 404, {}, ""
            if path.endswith("/config/downloadClient"):
                return 200, {"ok": True}, ""
            raise AssertionError(path)

        svc = self._service(fake_http)
        endpoint, payload = svc.fetch_download_client_config(
            "Readarr", "http://readarr", "/api/v1", "k"
        )
        self.assertEqual(endpoint, "/api/v1/config/downloadClient")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_ensure_download_handling_updates_when_drift_detected(self):
        requests = []

        def fake_http(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            requests.append((method, path, payload))
            if method == "GET":
                return (
                    200,
                    {
                        "enableCompletedDownloadHandling": False,
                        "removeCompletedDownloads": False,
                        "removeFailedDownloads": False,
                        "autoRedownloadFailed": False,
                    },
                    "",
                )
            if method == "PUT":
                return 200, {}, ""
            raise AssertionError(method)

        svc = self._service(fake_http)
        svc.ensure_download_handling(
            "Sonarr",
            "http://sonarr",
            "/api/v3",
            "key",
            {
                "enable_completed_download_handling": True,
                "remove_completed_downloads": True,
                "remove_failed_downloads": True,
                "auto_redownload_failed": True,
            },
        )
        put_calls = [c for c in requests if c[0] == "PUT"]
        self.assertEqual(len(put_calls), 1)
        payload = put_calls[0][2]
        self.assertTrue(payload["enableCompletedDownloadHandling"])
        self.assertTrue(payload["removeCompletedDownloads"])
        self.assertTrue(payload["removeFailedDownloads"])
        self.assertTrue(payload["autoRedownloadFailed"])

    def test_ensure_quality_upgrade_policy_updates_cutoff(self):
        requests = []
        selected_profile = {
            "id": 7,
            "cutoff": 99,
            "upgradeAllowed": False,
            "items": [
                {"quality": {"id": 1080, "name": "HD-1080p"}, "allowed": True},
                {"quality": {"id": 2160, "name": "Ultra-HD-2160p"}, "allowed": True},
            ],
        }

        def fake_http(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            requests.append((method, path, payload))
            if method == "PUT":
                return 200, {}, ""
            raise AssertionError(f"Unexpected HTTP call: {method} {path}")

        svc = self._service(fake_http, quality_profile=selected_profile)
        svc.ensure_quality_upgrade_policy(
            cfg={"quality_profiles": {}},
            app_cfg={"name": "Radarr", "implementation": "Radarr"},
            app_url="http://radarr:7878",
            api_base="/api/v3",
            api_key="key",
            quality_upgrade_cfg={
                "enabled": True,
                "allow_upgrades": True,
                "disallow_quality_name_tokens": ["2160", "4k"],
                "cutoff_preferred_name_tokens": ["1080"],
            },
        )
        put_calls = [c for c in requests if c[0] == "PUT"]
        self.assertEqual(len(put_calls), 1)
        payload = put_calls[0][2]
        self.assertEqual(payload["cutoff"], 1080)
        self.assertFalse(payload["items"][1]["allowed"])

    def test_media_management_applies_sonarr_series_folder_flag_case_insensitive(self):
        requests = []

        def fake_http(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            requests.append((method, path, payload))
            if method == "GET":
                return (
                    200,
                    {
                        "copyUsingHardlinks": True,
                        "createEmptySeriesFolders": False,
                    },
                    "",
                )
            if method == "PUT":
                return 200, {}, ""
            raise AssertionError(method)

        svc = self._service(fake_http)
        svc.ensure_media_management(
            app_cfg={
                "name": "Sonarr",
                "implementation": "sonarr",
                "capabilities": {"supports_series_folder_management": True},
            },
            app_url="http://sonarr:8989",
            api_base="/api/v3",
            api_key="key",
            media_cfg={"enabled": True, "create_empty_series_folders": True},
        )
        put_calls = [c for c in requests if c[0] == "PUT"]
        self.assertEqual(len(put_calls), 1)
        payload = put_calls[0][2]
        self.assertTrue(payload["createEmptySeriesFolders"])


if __name__ == "__main__":
    unittest.main()
