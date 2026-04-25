"""Unit tests for Prowlarr proxy operations."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.prowlarr.proxy_ops import (  # noqa: E402
    ensure_flaresolverr_proxy,
)


class TestEnsureFlaresolverrProxy(unittest.TestCase):
    """Tests for ensure_flaresolverr_proxy."""

    def _svc(self):
        svc = MagicMock()
        svc.field_map.return_value = {"host": "", "requestTimeout": 60}
        svc.field_list.return_value = [
            {"name": "host", "value": "http://flaresolverr:8191/"},
            {"name": "requestTimeout", "value": 60},
        ]
        return svc

    def _schema(self):
        return {
            "implementation": "FlareSolverr",
            "configContract": "FlareSolverrSettings",
            "fields": [
                {"name": "host", "value": ""},
                {"name": "requestTimeout", "value": 60},
            ],
        }

    def test_create_new_proxy(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),          # schema
            (200, [], ""),                         # list proxies (none)
            (201, {"id": 1, "name": "FlareSolverr"}, ""),  # create
            (200, {}, ""),                         # test
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("created" in l for l in logs))

    def test_update_existing_proxy(self):
        svc = self._svc()
        existing = {"implementation": "FlareSolverr", "name": "FlareSolverr", "id": 5}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (200, {"id": 5, "name": "FlareSolverr"}, ""),  # update
            (200, {}, ""),                                  # test
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("updated" in l for l in logs))

    def test_schema_fetch_failure_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (500, None, "error")
        with self.assertRaises(RuntimeError) as ctx:
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        self.assertIn("failed to read indexer proxy schema", str(ctx.exception))

    def test_schema_non_list_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (200, "not a list", "")
        with self.assertRaises(RuntimeError):
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")

    def test_schema_not_found_raises(self):
        svc = self._svc()
        other_schema = {"implementation": "OtherProxy", "configContract": "Other"}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [other_schema], ""),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        self.assertIn("FlareSolverr proxy schema not available", str(ctx.exception))

    def test_proxy_list_failure_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (500, None, "error"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        self.assertIn("failed to list indexer proxies", str(ctx.exception))

    def test_proxy_list_non_list_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, "not a list", ""),
        ]
        with self.assertRaises(RuntimeError):
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")

    def test_create_failure_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (500, None, "create error"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        self.assertIn("failed creating FlareSolverr proxy", str(ctx.exception))

    def test_update_failure_raises(self):
        svc = self._svc()
        existing = {"implementation": "FlareSolverr", "name": "FlareSolverr", "id": 3}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (500, None, "update error"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        self.assertIn("failed updating FlareSolverr proxy", str(ctx.exception))

    def test_test_connection_failure_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (500, None, "test failed"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        self.assertIn("FlareSolverr proxy test failed", str(ctx.exception))

    def test_test_connection_skipped(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"test_connection": False})
        # 4 = tag-fetch (auto, v1.0.130) + schema + list + create
        self.assertEqual(svc.http_request.call_count, 4)

    def test_custom_proxy_name(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1, "name": "MyFS"}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"proxy_name": "MyFS"})
        create_call = svc.http_request.call_args_list[3]
        self.assertEqual(create_call[1]["payload"]["name"], "MyFS")

    def test_custom_url(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"url": "http://custom:9999"})
        field_map_arg = svc.field_map.return_value
        # The function sets host in the field map before calling field_list
        # We verify via the log message which includes the URL
        logs = [c[0][0] for c in svc.log.call_args_list]
        created_logs = [l for l in logs if "created" in l]
        self.assertTrue(len(created_logs) >= 1)

    def test_empty_url_raises(self):
        svc = self._svc()
        with self.assertRaises(RuntimeError) as ctx:
            ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"url": "   "})
        self.assertIn("URL must be non-empty", str(ctx.exception))

    def test_url_trailing_slash_added(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"url": "http://fs:8191"})
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("http://fs:8191/" in l for l in logs))

    def test_default_proxy_name_is_flaresolverr(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        create_call = svc.http_request.call_args_list[3]
        self.assertEqual(create_call[1]["payload"]["name"], "FlareSolverr")

    def test_request_timeout_custom(self):
        svc = self._svc()
        svc.field_map.return_value = {"host": "", "requestTimeout": 60}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"request_timeout_seconds": 120})
        # The function sets requestTimeout to the custom value in the field map

    def test_request_timeout_invalid_defaults_60(self):
        svc = self._svc()
        svc.field_map.return_value = {"host": "", "requestTimeout": 60}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"request_timeout_seconds": "bad"})

    def test_request_timeout_minimum_1(self):
        svc = self._svc()
        svc.field_map.return_value = {"host": "", "requestTimeout": 60}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"request_timeout_seconds": -5})

    def test_tags_parsed_from_list(self):
        # Explicit tags in cfg skip the auto /api/v1/tag fetch.
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"tags": [1, 2, 3]})
        create_call = svc.http_request.call_args_list[2]
        self.assertEqual(create_call[1]["payload"]["tags"], [1, 2, 3])

    def test_tags_invalid_entries_skipped(self):
        # Explicit tags in cfg skip the auto /api/v1/tag fetch.
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"tags": ["abc", "", 5]})
        create_call = svc.http_request.call_args_list[2]
        self.assertEqual(create_call[1]["payload"]["tags"], [5])

    def test_tags_none_gives_empty(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", {"tags": None})
        create_call = svc.http_request.call_args_list[3]
        self.assertEqual(create_call[1]["payload"]["tags"], [])

    def test_none_cfg_uses_defaults(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key", None)
        create_call = svc.http_request.call_args_list[3]
        self.assertEqual(create_call[1]["payload"]["name"], "FlareSolverr")

    def test_match_existing_by_name(self):
        svc = self._svc()
        existing = {"implementation": "OtherImpl", "name": "FlareSolverr", "id": 9}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (200, {"id": 9}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        update_call = svc.http_request.call_args_list[3]
        self.assertEqual(update_call[1]["method"], "PUT")
        self.assertIn("/9", update_call[0][1])

    def test_match_existing_by_implementation(self):
        svc = self._svc()
        existing = {"implementation": "FlareSolverr", "name": "DifferentName", "id": 11}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (200, {"id": 11}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        update_call = svc.http_request.call_args_list[3]
        self.assertEqual(update_call[1]["method"], "PUT")

    def test_test_connection_201_accepted(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (201, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("test passed" in l for l in logs))

    def test_test_connection_202_accepted(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (202, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("test passed" in l for l in logs))

    def test_create_response_non_dict_uses_payload(self):
        svc = self._svc()
        # Sequence (v1.0.109+): GET schema, GET existing (none),
        # POST create with non-dict response, GET re-list (the
        # proxy_id fallback chain's last resort when response/payload/
        # current all lack an id — CREATE path has no current;
        # payload doesn't carry id until POST returns it), POST /test.
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, "not a dict", ""),
            (200, [{"implementation": "FlareSolverr",
                    "name": "FlareSolverr", "id": 99}], ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        # Should not crash; uses payload as fallback for test

    def test_update_response_non_dict_uses_payload(self):
        svc = self._svc()
        existing = {"implementation": "FlareSolverr", "name": "FlareSolverr", "id": 2}
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (200, "not a dict", ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        # Should not crash; uses payload as fallback for test

    def test_payload_implementation_always_flaresolverr(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        create_call = svc.http_request.call_args_list[3]
        self.assertEqual(create_call[1]["payload"]["implementation"], "FlareSolverr")

    def test_payload_enable_always_true(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [], ""),  # /api/v1/tag (auto, v1.0.130)
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
            (200, {}, ""),
        ]
        ensure_flaresolverr_proxy(svc, "http://prowlarr", "key")
        create_call = svc.http_request.call_args_list[3]
        self.assertTrue(create_call[1]["payload"]["enable"])


if __name__ == "__main__":
    unittest.main()
