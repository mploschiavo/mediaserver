"""Unit tests for Prowlarr indexer CRUD operations."""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.prowlarr.indexer_ops import (  # noqa: E402
    build_indexer_payload,
    ensure_indexer,
)


class TestBuildIndexerPayload(unittest.TestCase):
    """Tests for build_indexer_payload."""

    def _svc(self):
        return MagicMock()

    def test_basic_payload(self):
        svc = self._svc()
        template = {
            "name": "TestIdx",
            "implementation": "Newznab",
            "configContract": "NewznabSettings",
            "fields": [{"name": "baseUrl", "value": "http://example.com"}],
        }
        result = build_indexer_payload(svc, template)
        self.assertEqual(result["name"], "TestIdx")
        self.assertEqual(result["implementation"], "Newznab")
        self.assertTrue(result["enable"])
        self.assertEqual(result["priority"], 25)
        self.assertEqual(result["tags"], [])

    def test_defaults_enable_true(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y"})
        self.assertTrue(result["enable"])

    def test_defaults_priority_25(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y"})
        self.assertEqual(result["priority"], 25)

    def test_defaults_tags_empty(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y"})
        self.assertEqual(result["tags"], [])

    def test_defaults_fields_empty(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y"})
        self.assertEqual(result["fields"], [])

    def test_app_profile_id_defaults_to_1(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y"})
        self.assertEqual(result["appProfileId"], 1)

    def test_app_profile_id_zero_becomes_1(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "appProfileId": 0})
        self.assertEqual(result["appProfileId"], 1)

    def test_app_profile_id_negative_becomes_1(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "appProfileId": -5})
        self.assertEqual(result["appProfileId"], 1)

    def test_app_profile_id_valid_preserved(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "appProfileId": 3})
        self.assertEqual(result["appProfileId"], 3)

    def test_app_profile_id_string_invalid(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "appProfileId": "abc"})
        self.assertEqual(result["appProfileId"], 1)

    def test_app_profile_id_none_becomes_1(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "appProfileId": None})
        self.assertEqual(result["appProfileId"], 1)

    def test_enable_override_false(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "enable": False})
        self.assertFalse(result["enable"])

    def test_priority_override(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "priority": 50})
        self.assertEqual(result["priority"], 50)

    def test_tags_override(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "tags": [1, 2]})
        self.assertEqual(result["tags"], [1, 2])

    def test_disallowed_keys_excluded(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {
            "name": "X",
            "implementation": "Y",
            "hackerField": "bad",
            "id": 999,
        })
        self.assertNotIn("hackerField", result)
        self.assertNotIn("id", result)

    def test_none_value_excluded(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {"name": "X", "implementation": "Y", "redirect": None})
        self.assertNotIn("redirect", result)

    def test_allowed_keys_only(self):
        svc = self._svc()
        allowed = {
            "name", "implementation", "configContract", "fields", "priority",
            "tags", "appProfileId", "downloadClientId", "enable", "redirect",
            "enableRss", "enableAutomaticSearch", "enableInteractiveSearch",
        }
        template = {k: "val" for k in allowed}
        template["appProfileId"] = 5
        result = build_indexer_payload(svc, template)
        for key in allowed:
            self.assertIn(key, result)

    def test_rss_fields(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {
            "name": "X", "implementation": "Y",
            "enableRss": True, "enableAutomaticSearch": False,
            "enableInteractiveSearch": True,
        })
        self.assertTrue(result["enableRss"])
        self.assertFalse(result["enableAutomaticSearch"])
        self.assertTrue(result["enableInteractiveSearch"])

    def test_empty_template(self):
        svc = self._svc()
        result = build_indexer_payload(svc, {})
        self.assertTrue(result["enable"])
        self.assertEqual(result["priority"], 25)
        self.assertEqual(result["appProfileId"], 1)


class TestEnsureIndexer(unittest.TestCase):
    """Tests for ensure_indexer."""

    def _svc(self):
        svc = MagicMock()
        svc.field_map.return_value = {"baseUrl": "http://example.com"}
        svc.field_list.return_value = [{"name": "baseUrl", "value": "http://example.com"}]
        return svc

    def _schema(self, impl="Newznab"):
        return {
            "implementation": impl,
            "configContract": f"{impl}Settings",
            "fields": [{"name": "baseUrl", "value": ""}],
        }

    def test_create_new_indexer(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "New",
        })
        svc.log.assert_called_once()
        self.assertIn("created", svc.log.call_args[0][0])

    def test_update_existing_indexer(self):
        svc = self._svc()
        existing = {"implementation": "Newznab", "name": "Existing", "id": 5}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (200, {"id": 5}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Existing",
        })
        svc.log.assert_called_once()
        self.assertIn("updated", svc.log.call_args[0][0])

    def test_schema_fetch_failure_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (500, None, "error")
        with self.assertRaises(RuntimeError) as ctx:
            ensure_indexer(svc, "http://prowlarr", "key", {
                "implementation": "Newznab", "name": "X",
            })
        self.assertIn("failed to read indexer schema", str(ctx.exception))

    def test_schema_non_list_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (200, "not a list", "")
        with self.assertRaises(RuntimeError):
            ensure_indexer(svc, "http://prowlarr", "key", {
                "implementation": "Newznab", "name": "X",
            })

    def test_schema_not_found_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema("Torznab")], ""),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_indexer(svc, "http://prowlarr", "key", {
                "implementation": "Newznab", "name": "X",
            })
        self.assertIn("no indexer schema found", str(ctx.exception))

    def test_indexer_list_failure_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (500, None, "error"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_indexer(svc, "http://prowlarr", "key", {
                "implementation": "Newznab", "name": "X",
            })
        self.assertIn("failed to list indexers", str(ctx.exception))

    def test_indexer_list_non_list_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, "not a list", ""),
        ]
        with self.assertRaises(RuntimeError):
            ensure_indexer(svc, "http://prowlarr", "key", {
                "implementation": "Newznab", "name": "X",
            })

    def test_create_failure_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (500, None, "create error"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_indexer(svc, "http://prowlarr", "key", {
                "implementation": "Newznab", "name": "FailNew",
            })
        self.assertIn("failed to create", str(ctx.exception))

    def test_update_failure_raises(self):
        svc = self._svc()
        existing = {"implementation": "Newznab", "name": "FailUp", "id": 7}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (500, None, "update error"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_indexer(svc, "http://prowlarr", "key", {
                "implementation": "Newznab", "name": "FailUp",
            })
        self.assertIn("failed to update", str(ctx.exception))

    def test_field_overrides_applied(self):
        svc = self._svc()
        svc.field_map.return_value = {"baseUrl": "http://default.com"}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Override",
            "fields": {"baseUrl": "http://custom.com"},
        })
        call_args = svc.field_list.call_args[0][0]
        self.assertEqual(call_args["baseUrl"], "http://custom.com")

    def test_enable_defaults_true(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Def",
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertTrue(payload["enable"])

    def test_enable_override_false(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Dis", "enable": False,
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertFalse(payload["enable"])

    def test_priority_default_25(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Pri",
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertEqual(payload["priority"], 25)

    def test_priority_override(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Hi", "priority": 10,
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertEqual(payload["priority"], 10)

    def test_tags_default_empty(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Tags",
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertEqual(payload["tags"], [])

    def test_tags_override(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Tags", "tags": [1, 2],
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertEqual(payload["tags"], [1, 2])

    def test_config_contract_from_schema(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "CC",
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertEqual(payload["configContract"], "NewznabSettings")

    def test_config_contract_fallback(self):
        svc = self._svc()
        schema = {"implementation": "Newznab", "fields": []}
        svc.http_request.side_effect = [
            (200, [schema], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "FB",
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertEqual(payload["configContract"], "NewznabSettings")

    def test_update_uses_put(self):
        svc = self._svc()
        existing = {"implementation": "Newznab", "name": "PutTest", "id": 10}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (200, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "PutTest",
        })
        put_call = svc.http_request.call_args_list[2]
        self.assertEqual(put_call[1]["method"], "PUT")
        self.assertIn("/10", put_call[0][1])

    def test_create_uses_post(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "PostTest",
        })
        post_call = svc.http_request.call_args_list[2]
        self.assertEqual(post_call[1]["method"], "POST")

    def test_update_payload_includes_id(self):
        svc = self._svc()
        existing = {"implementation": "Newznab", "name": "IdTest", "id": 42}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (200, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "IdTest",
        })
        payload = svc.http_request.call_args[1]["payload"]
        self.assertEqual(payload["id"], 42)

    def test_http_202_on_update_succeeds(self):
        svc = self._svc()
        existing = {"implementation": "Newznab", "name": "Http202", "id": 8}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [existing], ""),
            (202, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Http202",
        })
        svc.log.assert_called_once()
        self.assertIn("updated", svc.log.call_args[0][0])

    def test_http_200_on_create_succeeds(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (200, {}, ""),
        ]
        ensure_indexer(svc, "http://prowlarr", "key", {
            "implementation": "Newznab", "name": "Http200",
        })
        svc.log.assert_called_once()
        self.assertIn("created", svc.log.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
