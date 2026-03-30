import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import parse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.prowlarr_service import ProwlarrService  # noqa: E402
from bootstrap_services.sabnzbd_service import SabnzbdService  # noqa: E402


def _field_map(fields):
    result = {}
    for field in fields or []:
        if isinstance(field, dict) and "name" in field:
            result[str(field["name"])] = field.get("value")
    return result


def _field_list(values):
    return [{"name": key, "value": value} for key, value in values.items()]


class SabnzbdServiceTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.requests = []

    def _service(self):
        def http_request(base_url, path, timeout=20):
            self.requests.append((base_url, path, timeout))
            query = parse.parse_qs(parse.urlparse(path).query)
            mode = (query.get("mode") or [""])[0]
            section = (query.get("section") or [""])[0]
            keyword = (query.get("keyword") or [""])[0]

            if mode == "get_config" and section == "misc":
                return 200, {
                    "config": {
                        "misc": {
                            "download_dir": "/wrong/incomplete",
                            "complete_dir": "/wrong/completed",
                            "auto_browser": "1",
                        }
                    }
                }, ""
            if mode == "get_config" and section == "categories":
                return 200, {"config": {"categories": []}}, ""
            if mode == "set_config" and section == "misc" and keyword:
                return 200, {"status": True}, ""
            if mode == "set_config" and section == "categories":
                return 200, {"status": True}, ""
            if mode == "get_cats":
                return 200, {"categories": []}, ""
            return 400, {}, f"unhandled {path}"

        return SabnzbdService(
            http_request=http_request,
            normalize_url=lambda value: value.rstrip("/"),
            normalize_mapping_path=lambda value: str(value or "").rstrip("/"),
            choose_category=lambda app, _cfg: str(app.get("category", "")).strip(),
            coerce_list=lambda value: value if isinstance(value, list) else [],
            resolve_path=lambda root, rel: Path(root) / rel,
            log=self.logs.append,
        )

    def test_read_api_key_prefers_env(self):
        service = self._service()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"SABNZBD_API_KEY": "from-env-key"},
            clear=False,
        ):
            value = service.read_api_key(tmp, {})
        self.assertEqual(value, "from-env-key")
        self.assertTrue(any("using API key from env" in line for line in self.logs))

    def test_ensure_defaults_sets_changed_values(self):
        service = self._service()
        service.ensure_defaults(
            sab_cfg={
                "url": "http://sabnzbd:8080",
                "incomplete_dir": "/data/usenet/incomplete",
                "complete_dir": "/data/usenet/completed",
                "auto_browser": False,
            },
            sab_api_key="abc123",
        )

        set_requests = [
            path
            for _, path, _ in self.requests
            if "mode=set_config" in path and "section=misc" in path
        ]
        self.assertEqual(len(set_requests), 3)
        self.assertTrue(any("set download_dir=/data/usenet/incomplete" in line for line in self.logs))
        self.assertTrue(any("set complete_dir=/data/usenet/completed" in line for line in self.logs))

    def test_ensure_categories_deduplicates_category_writes(self):
        service = self._service()
        service.ensure_categories(
            arr_apps=[
                {"name": "Sonarr", "category": "tv"},
                {"name": "Readarr", "category": "books"},
                {"name": "Readarr duplicate", "category": "books"},
            ],
            sab_cfg={
                "url": "http://sabnzbd:8080",
                "complete_dir": "/data/usenet/completed",
            },
            sab_api_key="abc123",
        )

        set_category_requests = [
            path
            for _, path, _ in self.requests
            if "mode=set_config" in path and "section=categories" in path
        ]
        self.assertEqual(len(set_category_requests), 2)
        self.assertTrue(any("created category tv" in line for line in self.logs))
        self.assertTrue(any("created category books" in line for line in self.logs))


class ProwlarrServiceTests(unittest.TestCase):
    def setUp(self):
        self.logs = []
        self.calls = []

    def _service_with_stub(self, stub):
        def http_request(base_url, path, api_key=None, method="GET", payload=None):
            self.calls.append(
                {
                    "base_url": base_url,
                    "path": path,
                    "api_key": api_key,
                    "method": method,
                    "payload": payload,
                }
            )
            return stub(base_url, path, api_key, method, payload)

        return ProwlarrService(
            http_request=http_request,
            field_map=_field_map,
            field_list=_field_list,
            log=self.logs.append,
        )

    def test_ensure_application_fallback_without_sync_level(self):
        put_attempts = []

        def stub(_base_url, path, _api_key, method, payload):
            if path == "/api/v1/applications/schema" and method == "GET":
                return 200, [
                    {
                        "implementation": "Sonarr",
                        "configContract": "SonarrSettings",
                        "fields": [
                            {"name": "baseUrl", "value": ""},
                            {"name": "apiKey", "value": ""},
                        ],
                    }
                ], ""
            if path == "/api/v1/applications" and method == "GET":
                return 200, [
                    {
                        "id": 42,
                        "implementation": "Sonarr",
                        "fields": [{"name": "baseUrl", "value": "http://sonarr:8989"}],
                    }
                ], ""
            if path == "/api/v1/applications/42" and method == "PUT":
                put_attempts.append(payload)
                if len(put_attempts) == 1:
                    return 400, {}, "syncLevel rejected"
                return 202, {}, ""
            return 500, {}, f"unexpected {method} {path}"

        service = self._service_with_stub(stub)
        service.ensure_application(
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="key",
            app_name="Sonarr",
            implementation="Sonarr",
            app_url="http://sonarr:8989",
            app_key="arr-key",
        )

        self.assertEqual(len(put_attempts), 2)
        self.assertIn("syncLevel", put_attempts[0])
        self.assertNotIn("syncLevel", put_attempts[1])
        self.assertTrue(any("updated application link for Sonarr" in line for line in self.logs))

    def test_auto_add_tested_indexers_skips_existing_and_adds_new(self):
        def stub(_base_url, path, _api_key, method, payload):
            if path == "/api/v1/indexer/schema" and method == "GET":
                return 200, [
                    {"implementation": "A", "name": "Existing", "fields": []},
                    {"implementation": "B", "name": "NewOne", "fields": []},
                ], ""
            if path == "/api/v1/indexer" and method == "GET":
                return 200, [{"implementation": "A", "name": "Existing"}], ""
            if path == "/api/v1/indexer/test" and method == "POST":
                if payload and payload.get("name") == "NewOne":
                    return 200, {}, ""
                return 400, {}, "test failed"
            if path == "/api/v1/indexer" and method == "POST":
                return 201, {}, ""
            return 500, {}, f"unexpected {method} {path}"

        service = self._service_with_stub(stub)
        service.auto_add_tested_indexers(
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="key",
        )

        self.assertTrue(any(line == "[ADD] NewOne" for line in self.logs))
        self.assertTrue(any("Auto indexer summary" in line for line in self.logs))


if __name__ == "__main__":
    unittest.main()
