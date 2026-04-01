import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import bootstrap_services.apps.servarr.runtime.arr_ops as MODULE
import bootstrap_services.apps.servarr.runtime.arr_ops as SERVARR_ARR_OPS


class ReadarrMetadataSourceTests(unittest.TestCase):
    def test_updates_metadata_source_when_different(self):
        calls = []

        def fake_http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if path.endswith("/config/development") and method == "GET":
                return 200, {"id": 1, "metadataSource": ""}, ""
            if path.endswith("/config/development") and method == "PUT":
                return 202, {"id": 1, "metadataSource": "https://api.bookinfo.pro"}, ""
            raise AssertionError(f"Unexpected request: {method} {path}")

        cfg = {"readarr": {"metadata_source": "https://api.bookinfo.pro"}}
        app_cfg = {"name": "Readarr", "implementation": "Readarr"}

        with mock.patch.object(SERVARR_ARR_OPS, "http_request", side_effect=fake_http_request):
            MODULE.ensure_readarr_metadata_source(
                cfg=cfg,
                app_cfg=app_cfg,
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="readarr-key",
            )

        put_calls = [item for item in calls if item[0] == "PUT"]
        self.assertEqual(len(put_calls), 1)
        self.assertEqual(put_calls[0][2].get("metadataSource"), "https://api.bookinfo.pro")

    def test_no_update_when_already_set(self):
        calls = []

        def fake_http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
            del base_url, api_key, timeout
            calls.append((method, path, payload))
            if path.endswith("/config/development") and method == "GET":
                return 200, {"id": 1, "metadataSource": "https://api.bookinfo.pro"}, ""
            raise AssertionError(f"Unexpected request: {method} {path}")

        cfg = {"readarr": {"metadata_source": "https://api.bookinfo.pro"}}
        app_cfg = {"name": "Readarr", "implementation": "Readarr"}

        with mock.patch.object(SERVARR_ARR_OPS, "http_request", side_effect=fake_http_request):
            MODULE.ensure_readarr_metadata_source(
                cfg=cfg,
                app_cfg=app_cfg,
                app_url="http://readarr:8787",
                api_base="/api/v1",
                api_key="readarr-key",
            )

        put_calls = [item for item in calls if item[0] == "PUT"]
        self.assertEqual(len(put_calls), 0)


if __name__ == "__main__":
    unittest.main()
