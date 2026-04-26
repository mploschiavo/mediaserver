import unittest

from media_stack.services.apps.servarr.arr_queue_cleanup_service import ArrQueueCleanupService


def _bool_cfg(cfg, key, default):
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _to_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def _normalize_token(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _resolve_arr_overrides_by_app(_queue_cfg, _app_cfg):
    return {}


class ArrQueueCleanupServiceTests(unittest.TestCase):
    def _service(self, http_request):
        return ArrQueueCleanupService(
            http_request=http_request,
            bool_cfg=_bool_cfg,
            coerce_list=_coerce_list,
            to_int=_to_int,
            normalize_token=_normalize_token,
            resolve_arr_overrides_by_app=_resolve_arr_overrides_by_app,
            log=lambda _msg: None,
        )

    def test_arr_queue_records_supports_common_shapes(self):
        svc = self._service(lambda *_args, **_kwargs: (200, {}, ""))
        self.assertEqual(svc.arr_queue_records({"records": [1, 2]}), [1, 2])
        self.assertEqual(svc.arr_queue_records({"Items": ["a"]}), ["a"])
        self.assertEqual(svc.arr_queue_records([{"id": 1}]), [{"id": 1}])
        self.assertEqual(svc.arr_queue_records({"unknown": []}), [])

    def test_queue_item_is_failed_detects_status_messages(self):
        svc = self._service(lambda *_args, **_kwargs: (200, {}, ""))
        item = {
            "status": "completed",
            "statusMessages": [
                {"title": "Import failed", "messages": ["Path missing"]},
            ],
        }
        self.assertTrue(svc.queue_item_is_failed(item, [_normalize_token("import failed")]))

    def test_ensure_arr_failed_queue_cleanup_deletes_failed_items(self):
        calls = {"delete": []}

        def fake_http(_base, path, api_key=None, method="GET", payload=None, timeout=20):
            del api_key, payload, timeout
            if method == "DELETE":
                calls["delete"].append(path)
                return 200, {}, ""
            if "/queue?page=1&pageSize=" in path:
                return (
                    200,
                    {
                        "records": [
                            {"id": 11, "status": "failed"},
                            {"id": 12, "status": "warning"},
                            {"id": 13, "status": "completed"},
                        ]
                    },
                    "",
                )
            return 404, {}, "not found"

        svc = self._service(fake_http)
        deleted = svc.ensure_arr_failed_queue_cleanup(
            app_cfg={"name": "Sonarr", "implementation": "sonarr"},
            app_url="http://sonarr:8989",
            api_base="/api/v3",
            api_key="abc",
            hygiene_cfg={
                "arr_failed_queue_cleanup": {
                    "enabled": True,
                    "max_delete_per_run": 1,
                    "failed_status_tokens": ["failed", "warning"],
                }
            },
        )

        self.assertEqual(deleted, 1)
        self.assertEqual(len(calls["delete"]), 1)
        self.assertIn("/api/v3/queue/11", calls["delete"][0])


if __name__ == "__main__":
    unittest.main()
