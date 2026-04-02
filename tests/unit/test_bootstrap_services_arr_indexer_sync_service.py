import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from bootstrap_services.arr_indexer_sync_service import ArrIndexerSyncService  # noqa: E402


class ArrIndexerSyncServiceTests(unittest.TestCase):
    def _service(self, stub):
        def http_request(base_url, path, api_key=None, method="GET", payload=None):
            return stub(base_url, path, api_key, method, payload)

        return ArrIndexerSyncService(
            http_request=http_request,
            detect_arr_api_base=lambda _name, _url, _key: "/api/v3",
            log=lambda _msg: None,
        )

    def test_reconcile_removes_stale_prowlarr_indexers(self):
        deleted_ids = []

        def stub(_base, path, _key, method, _payload):
            if path == "/api/v1/indexer" and method == "GET":
                return 200, [{"name": "Good", "enable": True}], ""
            if path == "/api/v3/indexer" and method == "GET":
                return (
                    200,
                    [
                        {"id": 10, "name": "Good", "implementationName": "Prowlarr"},
                        {"id": 11, "name": "Old", "implementationName": "Prowlarr"},
                    ],
                    "",
                )
            if path == "/api/v3/indexer/11" and method == "DELETE":
                deleted_ids.append(11)
                return 200, {}, ""
            return 500, {}, f"unexpected {method} {path}"

        svc = self._service(stub)
        summary = svc.reconcile(
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="k",
            arr_apps=[{"name": "Sonarr", "implementation": "sonarr", "url": "http://sonarr:8989"}],
            app_keys={"sonarr": "arr-key"},
            prune_stale=True,
        )
        self.assertEqual(summary["stale_found"], 1)
        self.assertEqual(summary["stale_removed"], 1)
        self.assertEqual(deleted_ids, [11])

    def test_reconcile_dry_run_keeps_stale_indexers(self):
        delete_called = False

        def stub(_base, path, _key, method, _payload):
            nonlocal delete_called
            if path == "/api/v1/indexer" and method == "GET":
                return 200, [{"name": "Good", "enable": True}], ""
            if path == "/api/v3/indexer" and method == "GET":
                return (
                    200,
                    [
                        {"id": 11, "name": "Old", "implementationName": "Prowlarr"},
                    ],
                    "",
                )
            if path == "/api/v3/indexer/11" and method == "DELETE":
                delete_called = True
                return 200, {}, ""
            return 500, {}, f"unexpected {method} {path}"

        svc = self._service(stub)
        summary = svc.reconcile(
            prowlarr_url="http://prowlarr:9696",
            prowlarr_key="k",
            arr_apps=[{"name": "Sonarr", "implementation": "sonarr", "url": "http://sonarr:8989"}],
            app_keys={"sonarr": "arr-key"},
            prune_stale=False,
        )
        self.assertEqual(summary["stale_found"], 1)
        self.assertEqual(summary["stale_removed"], 0)
        self.assertEqual(summary["stale_kept"], 1)
        self.assertFalse(delete_called)


if __name__ == "__main__":
    unittest.main()
