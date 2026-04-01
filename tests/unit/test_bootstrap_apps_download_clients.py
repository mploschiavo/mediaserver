import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap_services.apps.servarr.runtime.arr_ops as MODULE
import bootstrap_services.apps.servarr.runtime.factory as SERVARR_FACTORY


class DownloadClientTests(unittest.TestCase):
    def test_priority_is_clamped_to_valid_range(self):
        calls = []

        schema_payload = [
            {
                "implementation": "QBittorrent",
                "configContract": "QBittorrentSettings",
                "fields": [
                    {"name": "host", "value": ""},
                    {"name": "port", "value": 8080},
                    {"name": "username", "value": ""},
                    {"name": "password", "value": ""},
                    {"name": "priority", "value": 0},
                    {"name": "recentTvPriority", "value": 0},
                    {"name": "olderTvPriority", "value": 0},
                    {"name": "category", "value": ""},
                ],
            }
        ]
        existing_clients = [
            {
                "id": 7,
                "implementation": "QBittorrent",
                "name": "qBittorrent",
                "fields": [
                    {"name": "host", "value": "qbittorrent"},
                    {"name": "port", "value": 8080},
                ],
            }
        ]

        def fake_http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if path.endswith("/downloadclient/schema"):
                return 200, schema_payload, ""
            if path.endswith("/downloadclient") and method == "GET":
                return 200, existing_clients, ""
            if path.endswith("/downloadclient/7") and method == "PUT":
                return 200, {}, ""
            raise AssertionError(f"Unexpected request: {method} {path}")

        app_cfg = {
            "name": "Readarr",
            "implementation": "Readarr",
            "capabilities": {"download_client_dual_priority_fields": True},
        }
        client_cfg = {
            "implementation": "QBittorrent",
            "name": "qBittorrent",
            "host": "qbittorrent",
            "port": 8080,
            "priority": 0,
        }
        client_auth = {"username": "admin", "password": "secret"}

        with mock.patch.object(SERVARR_FACTORY, "http_request", side_effect=fake_http_request):
            MODULE.ensure_arr_download_client(
                app_cfg=app_cfg,
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="readarr-api-key",
                client_cfg=client_cfg,
                client_auth=client_auth,
            )

        put_calls = [
            item for item in calls if item[0] == "PUT" and item[1].endswith("/downloadclient/7")
        ]
        self.assertEqual(len(put_calls), 1)
        payload = put_calls[0][2]
        self.assertEqual(payload.get("priority"), 1)
        self.assertEqual(payload.get("Priority"), 1)
        fields = {field["name"]: field.get("value") for field in payload.get("fields", [])}
        self.assertEqual(fields.get("priority"), 1)
        self.assertEqual(fields.get("Priority"), 1)
        self.assertEqual(fields.get("recentTvPriority"), 1)
        self.assertEqual(fields.get("olderTvPriority"), 1)

    def test_readarr_priority_validation_retries_with_fixed_payload(self):
        calls = []
        put_attempt = {"count": 0}

        schema_payload = [
            {
                "implementation": "QBittorrent",
                "configContract": "QBittorrentSettings",
                "fields": [
                    {"name": "host", "value": ""},
                    {"name": "port", "value": 8080},
                    {"name": "username", "value": ""},
                    {"name": "password", "value": ""},
                    {"name": "recentTvPriority", "value": 0},
                    {"name": "olderTvPriority", "value": 0},
                    {"name": "category", "value": ""},
                ],
            }
        ]
        existing_clients = [
            {
                "id": 3,
                "implementation": "QBittorrent",
                "name": "qBittorrent",
                "fields": [
                    {"name": "host", "value": "qbittorrent"},
                    {"name": "port", "value": 8080},
                ],
            }
        ]
        validation_body = (
            '[{"propertyName":"Priority",'
            '"errorMessage":"\'Priority\' must be between 1 and 50. You entered 0.",'
            '"errorCode":"InclusiveBetweenValidator"}]'
        )

        def fake_http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if path.endswith("/downloadclient/schema"):
                return 200, schema_payload, ""
            if path.endswith("/downloadclient") and method == "GET":
                return 200, existing_clients, ""
            if path.endswith("/downloadclient/3") and method == "PUT":
                put_attempt["count"] += 1
                if put_attempt["count"] == 1:
                    return 400, {}, validation_body
                return 200, {}, ""
            raise AssertionError(f"Unexpected request: {method} {path}")

        app_cfg = {
            "name": "Readarr",
            "implementation": "Readarr",
            "capabilities": {"download_client_dual_priority_fields": True},
        }
        client_cfg = {
            "implementation": "QBittorrent",
            "name": "qBittorrent",
            "host": "qbittorrent",
            "port": 8080,
        }
        client_auth = {"username": "admin", "password": "secret"}

        with mock.patch.object(SERVARR_FACTORY, "http_request", side_effect=fake_http_request):
            MODULE.ensure_arr_download_client(
                app_cfg=app_cfg,
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="readarr-api-key",
                client_cfg=client_cfg,
                client_auth=client_auth,
            )

        self.assertEqual(put_attempt["count"], 2)
        put_calls = [
            item for item in calls if item[0] == "PUT" and item[1].endswith("/downloadclient/3")
        ]
        retry_payload = put_calls[-1][2]
        self.assertEqual(retry_payload.get("priority"), 1)
        self.assertEqual(retry_payload.get("Priority"), 1)
        retry_fields = {
            field["name"]: field.get("value") for field in retry_payload.get("fields", [])
        }
        self.assertEqual(retry_fields.get("priority"), 1)
        self.assertEqual(retry_fields.get("Priority"), 1)
        self.assertEqual(retry_fields.get("recentTvPriority"), 1)
        self.assertEqual(retry_fields.get("olderTvPriority"), 1)

    def test_priority_alias_is_capability_driven_not_app_name(self):
        calls = []

        schema_payload = [
            {
                "implementation": "QBittorrent",
                "configContract": "QBittorrentSettings",
                "fields": [
                    {"name": "host", "value": ""},
                    {"name": "port", "value": 8080},
                    {"name": "username", "value": ""},
                    {"name": "password", "value": ""},
                    {"name": "priority", "value": 0},
                    {"name": "category", "value": ""},
                ],
            }
        ]
        existing_clients = [
            {
                "id": 11,
                "implementation": "QBittorrent",
                "name": "qBittorrent",
                "fields": [
                    {"name": "host", "value": "qbittorrent"},
                    {"name": "port", "value": 8080},
                ],
            }
        ]

        def fake_http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if path.endswith("/downloadclient/schema"):
                return 200, schema_payload, ""
            if path.endswith("/downloadclient") and method == "GET":
                return 200, existing_clients, ""
            if path.endswith("/downloadclient/11") and method == "PUT":
                return 200, {}, ""
            raise AssertionError(f"Unexpected request: {method} {path}")

        app_cfg = {"name": "Readarr", "implementation": "Readarr"}
        client_cfg = {
            "implementation": "QBittorrent",
            "name": "qBittorrent",
            "host": "qbittorrent",
            "port": 8080,
            "priority": 5,
        }
        client_auth = {"username": "admin", "password": "secret"}

        with mock.patch.object(SERVARR_FACTORY, "http_request", side_effect=fake_http_request):
            MODULE.ensure_arr_download_client(
                app_cfg=app_cfg,
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="readarr-api-key",
                client_cfg=client_cfg,
                client_auth=client_auth,
            )

        put_calls = [
            item for item in calls if item[0] == "PUT" and item[1].endswith("/downloadclient/11")
        ]
        self.assertEqual(len(put_calls), 1)
        payload = put_calls[0][2]
        self.assertEqual(payload.get("priority"), 5)
        self.assertNotIn("Priority", payload)
        fields = {field["name"]: field.get("value") for field in payload.get("fields", [])}
        self.assertEqual(fields.get("priority"), 5)
        self.assertNotIn("Priority", fields)


if __name__ == "__main__":
    unittest.main()
