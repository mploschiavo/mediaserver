"""Unit tests for Prowlarr application link operations."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.prowlarr.application_ops import (  # noqa: E402
    _is_name_unique_conflict,
    ensure_application,
    find_existing_application,
    find_existing_application_by_name,
    resolve_schema_contract,
    trigger_sync,
)


class TestResolveSchemaContract(unittest.TestCase):
    """Tests for resolve_schema_contract."""

    def _svc(self):
        return MagicMock()

    def test_found_schema(self):
        svc = self._svc()
        schema = {"implementation": "Sonarr", "configContract": "SonarrSettings"}
        svc.http_request.return_value = (200, [schema], "")
        result = resolve_schema_contract(svc, "http://prowlarr", "key", "Sonarr")
        self.assertEqual(result, schema)

    def test_schema_not_found_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (200, [{"implementation": "Radarr"}], "")
        with self.assertRaises(RuntimeError) as ctx:
            resolve_schema_contract(svc, "http://prowlarr", "key", "Sonarr")
        self.assertIn("no application schema found", str(ctx.exception))

    def test_schema_fetch_failure_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (500, None, "error")
        with self.assertRaises(RuntimeError) as ctx:
            resolve_schema_contract(svc, "http://prowlarr", "key", "Sonarr")
        self.assertIn("failed to read application schema", str(ctx.exception))

    def test_schema_non_list_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (200, "not a list", "")
        with self.assertRaises(RuntimeError):
            resolve_schema_contract(svc, "http://prowlarr", "key", "Sonarr")

    def test_multiple_schemas_returns_correct(self):
        svc = self._svc()
        schemas = [
            {"implementation": "Radarr", "configContract": "RadarrSettings"},
            {"implementation": "Sonarr", "configContract": "SonarrSettings"},
            {"implementation": "Lidarr", "configContract": "LidarrSettings"},
        ]
        svc.http_request.return_value = (200, schemas, "")
        result = resolve_schema_contract(svc, "http://prowlarr", "key", "Sonarr")
        self.assertEqual(result["implementation"], "Sonarr")


class TestFindExistingApplication(unittest.TestCase):
    """Tests for find_existing_application."""

    def _svc(self):
        svc = MagicMock()
        svc.field_map.return_value = {"baseUrl": "http://sonarr:8989"}
        return svc

    def test_found_by_url(self):
        svc = self._svc()
        app = {"implementation": "Sonarr", "name": "Sonarr", "id": 1, "fields": []}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application(svc, "http://prowlarr", "key", "Sonarr", "http://sonarr:8989")
        self.assertEqual(result, app)

    def test_not_found_wrong_impl(self):
        svc = self._svc()
        app = {"implementation": "Radarr", "name": "Radarr", "id": 1, "fields": []}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application(svc, "http://prowlarr", "key", "Sonarr", "http://sonarr:8989")
        self.assertIsNone(result)

    def test_not_found_wrong_url(self):
        svc = self._svc()
        svc.field_map.return_value = {"baseUrl": "http://other:9999"}
        app = {"implementation": "Sonarr", "name": "Sonarr", "id": 1, "fields": []}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application(svc, "http://prowlarr", "key", "Sonarr", "http://sonarr:8989")
        self.assertIsNone(result)

    def test_url_trailing_slash_normalized(self):
        svc = self._svc()
        svc.field_map.return_value = {"baseUrl": "http://sonarr:8989/"}
        app = {"implementation": "Sonarr", "name": "Sonarr", "id": 1, "fields": []}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application(svc, "http://prowlarr", "key", "Sonarr", "http://sonarr:8989/")
        self.assertEqual(result, app)

    def test_list_failure_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (500, None, "error")
        with self.assertRaises(RuntimeError):
            find_existing_application(svc, "http://prowlarr", "key", "Sonarr", "http://sonarr:8989")

    def test_list_non_list_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (200, "not a list", "")
        with self.assertRaises(RuntimeError):
            find_existing_application(svc, "http://prowlarr", "key", "Sonarr", "http://sonarr:8989")

    def test_empty_list_returns_none(self):
        svc = self._svc()
        svc.http_request.return_value = (200, [], "")
        result = find_existing_application(svc, "http://prowlarr", "key", "Sonarr", "http://sonarr:8989")
        self.assertIsNone(result)


class TestFindExistingApplicationByName(unittest.TestCase):
    """Tests for find_existing_application_by_name."""

    def _svc(self):
        return MagicMock()

    def test_found_by_name(self):
        svc = self._svc()
        app = {"implementation": "Sonarr", "name": "Sonarr", "id": 1}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application_by_name(svc, "http://prowlarr", "key", "Sonarr", "Sonarr")
        self.assertEqual(result, app)

    def test_name_case_insensitive(self):
        svc = self._svc()
        app = {"implementation": "Sonarr", "name": "SONARR", "id": 1}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application_by_name(svc, "http://prowlarr", "key", "Sonarr", "sonarr")
        self.assertEqual(result, app)

    def test_not_found_wrong_name(self):
        svc = self._svc()
        app = {"implementation": "Sonarr", "name": "Other", "id": 1}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application_by_name(svc, "http://prowlarr", "key", "Sonarr", "Sonarr")
        self.assertIsNone(result)

    def test_not_found_wrong_impl(self):
        svc = self._svc()
        app = {"implementation": "Radarr", "name": "Sonarr", "id": 1}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application_by_name(svc, "http://prowlarr", "key", "Sonarr", "Sonarr")
        self.assertIsNone(result)

    def test_list_failure_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (500, None, "error")
        with self.assertRaises(RuntimeError):
            find_existing_application_by_name(svc, "http://prowlarr", "key", "Sonarr", "Sonarr")

    def test_empty_list_returns_none(self):
        svc = self._svc()
        svc.http_request.return_value = (200, [], "")
        result = find_existing_application_by_name(svc, "http://prowlarr", "key", "Sonarr", "Sonarr")
        self.assertIsNone(result)

    def test_name_whitespace_stripped(self):
        svc = self._svc()
        app = {"implementation": "Sonarr", "name": "  Sonarr  ", "id": 1}
        svc.http_request.return_value = (200, [app], "")
        result = find_existing_application_by_name(svc, "http://prowlarr", "key", "Sonarr", "  Sonarr  ")
        self.assertEqual(result, app)


class TestIsNameUniqueConflict(unittest.TestCase):
    """Tests for _is_name_unique_conflict."""

    def test_not_400_returns_false(self):
        self.assertFalse(_is_name_unique_conflict(200, ""))
        self.assertFalse(_is_name_unique_conflict(500, "name should be unique"))

    def test_400_with_name_should_be_unique(self):
        self.assertTrue(_is_name_unique_conflict(400, "Name should be unique"))

    def test_400_with_structured_error(self):
        body = json.dumps([{"propertyName": "name", "errorMessage": "Should be unique"}])
        self.assertTrue(_is_name_unique_conflict(400, body))

    def test_400_with_should_be_unique_and_propertyname_in_text(self):
        self.assertTrue(_is_name_unique_conflict(400, "should be unique propertyname name"))

    def test_400_with_unrelated_error(self):
        self.assertFalse(_is_name_unique_conflict(400, "some other error"))

    def test_400_with_json_wrong_property(self):
        # Body must avoid text-level "should be unique" + "propertyname" + "name" match
        body = json.dumps([{"field": "url", "msg": "Must be distinct"}])
        self.assertFalse(_is_name_unique_conflict(400, body))

    def test_400_with_json_no_unique_in_message(self):
        body = json.dumps([{"propertyName": "name", "errorMessage": "Is required"}])
        self.assertFalse(_is_name_unique_conflict(400, body))

    def test_400_with_non_list_json(self):
        # Dict body without "name should be unique" text avoids text match
        body = json.dumps({"error": "some validation error"})
        self.assertFalse(_is_name_unique_conflict(400, body))

    def test_400_with_invalid_json(self):
        self.assertFalse(_is_name_unique_conflict(400, "not json at all {{{"))

    def test_400_with_empty_body(self):
        self.assertFalse(_is_name_unique_conflict(400, ""))

    def test_400_with_none_body(self):
        self.assertFalse(_is_name_unique_conflict(400, None))

    def test_400_with_mixed_list_items(self):
        body = json.dumps([
            "not a dict",
            {"propertyName": "name", "errorMessage": "Should be unique"},
        ])
        self.assertTrue(_is_name_unique_conflict(400, body))


class TestEnsureApplication(unittest.TestCase):
    """Tests for ensure_application."""

    def _svc(self):
        svc = MagicMock()
        svc.field_map.return_value = {"baseUrl": "", "apiKey": "", "prowlarrUrl": ""}
        svc.field_list.return_value = [
            {"name": "baseUrl", "value": "http://sonarr:8989"},
            {"name": "apiKey", "value": "key123"},
        ]
        return svc

    def _schema(self, impl="Sonarr"):
        return {
            "implementation": impl,
            "configContract": f"{impl}Settings",
            "fields": [
                {"name": "baseUrl", "value": ""},
                {"name": "apiKey", "value": ""},
                {"name": "prowlarrUrl", "value": ""},
            ],
        }

    def test_create_new_application(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),           # schema
            (200, [], ""),                          # find existing (none)
            (201, {"id": 1}, ""),                   # create
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("created" in l for l in logs))

    def test_update_existing_application(self):
        svc = self._svc()
        # Call order: find_existing_application.field_map, ensure_application.field_map
        svc.field_map.side_effect = [
            {"baseUrl": "http://sonarr:8989"},                  # find_existing_application
            {"baseUrl": "", "apiKey": "", "prowlarrUrl": ""},   # ensure_application payload
        ]
        existing = {"implementation": "Sonarr", "name": "Sonarr", "id": 5,
                     "fields": [{"name": "baseUrl", "value": "http://sonarr:8989"}]}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),   # schema
            (200, [existing], ""),          # list apps
            (200, {"id": 5}, ""),           # PUT update
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("updated" in l for l in logs))

    def test_create_failure_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),   # schema
            (200, [], ""),                  # find existing (none)
            (500, None, "error"),           # POST create fails
            (500, None, "error"),           # syncLevel fallback also fails
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                               "http://sonarr:8989", "skey")
        self.assertIn("failed creating app", str(ctx.exception))

    def test_update_failure_raises(self):
        svc = self._svc()
        svc.field_map.side_effect = [
            {"baseUrl": "http://sonarr:8989"},                  # find_existing_application
            {"baseUrl": "", "apiKey": "", "prowlarrUrl": ""},   # ensure_application payload
        ]
        existing = {"implementation": "Sonarr", "name": "Sonarr", "id": 5,
                     "fields": [{"name": "baseUrl", "value": "http://sonarr:8989"}]}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),   # schema
            (200, [existing], ""),          # list apps
            (500, None, "err1"),            # PUT update fails
            (500, None, "err2"),            # syncLevel fallback also fails
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                               "http://sonarr:8989", "skey")
        self.assertIn("failed updating app", str(ctx.exception))

    def test_synclevel_fallback_on_create(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (400, None, "syncLevel bad"),
            (201, {"id": 1}, ""),  # fallback POST without syncLevel
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("created" in l for l in logs))

    def test_synclevel_fallback_on_update(self):
        svc = self._svc()
        svc.field_map.side_effect = [
            {"baseUrl": "http://sonarr:8989"},                  # find_existing_application
            {"baseUrl": "", "apiKey": "", "prowlarrUrl": ""},   # ensure_application payload
        ]
        existing = {"implementation": "Sonarr", "name": "Sonarr", "id": 5,
                     "fields": [{"name": "baseUrl", "value": "http://sonarr:8989"}]}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),   # schema
            (200, [existing], ""),          # list apps
            (400, None, "syncLevel bad"),   # PUT fails
            (200, {"id": 5}, ""),           # fallback PUT without syncLevel
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("updated" in l for l in logs))

    def test_name_unique_conflict_reconciles(self):
        svc = self._svc()
        dup = {"implementation": "Sonarr", "name": "Sonarr", "id": 99}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),                  # schema
            (200, [], ""),                                 # find existing by URL (none)
            (400, None, "Name should be unique"),          # POST create fails
            (400, None, "Name should be unique"),          # syncLevel fallback also fails
            (200, [dup], ""),                              # find by name
            (200, {"id": 99}, ""),                         # PUT update duplicate
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        logs = [c[0][0] for c in svc.log.call_args_list]
        self.assertTrue(any("reconciled" in l for l in logs))

    def test_name_unique_conflict_no_dup_found_raises(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),                  # schema
            (200, [], ""),                                 # find existing (none)
            (400, None, "Name should be unique"),          # POST create fails
            (400, None, "Name should be unique"),          # syncLevel fallback also fails
            (200, [], ""),                                 # find by name returns empty
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                               "http://sonarr:8989", "skey")
        self.assertIn("failed creating app", str(ctx.exception))

    def test_name_unique_conflict_dup_update_fails_raises(self):
        svc = self._svc()
        dup = {"implementation": "Sonarr", "name": "Sonarr", "id": 99}
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),                  # schema
            (200, [], ""),                                 # find existing (none)
            (400, None, "Name should be unique"),          # POST create fails
            (400, None, "Name should be unique"),          # syncLevel fallback also fails
            (200, [dup], ""),                              # find by name
            (500, None, "update failed"),                  # PUT update fails
            (500, None, "update failed"),                  # syncLevel fallback also fails
        ]
        with self.assertRaises(RuntimeError) as ctx:
            ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                               "http://sonarr:8989", "skey")
        self.assertIn("failed updating duplicate-name app", str(ctx.exception))

    def test_payload_sets_prowlarr_url(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        field_map = svc.field_map.return_value
        self.assertEqual(field_map["prowlarrUrl"], "http://prowlarr")

    def test_payload_sets_base_url(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "MySonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        field_map = svc.field_map.return_value
        self.assertEqual(field_map["baseUrl"], "http://sonarr:8989")

    def test_payload_sets_api_key(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "appkey123")
        field_map = svc.field_map.return_value
        self.assertEqual(field_map["apiKey"], "appkey123")

    def test_payload_sync_level(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        create_call = svc.http_request.call_args_list[2]
        self.assertEqual(create_call[1]["payload"]["syncLevel"], "fullSync")

    def test_schema_resolution_called(self):
        svc = self._svc()
        svc.http_request.side_effect = [
            (200, [self._schema()], ""),
            (200, [], ""),
            (201, {"id": 1}, ""),
        ]
        ensure_application(svc, "http://prowlarr", "pkey", "Sonarr", "Sonarr",
                           "http://sonarr:8989", "skey")
        schema_call = svc.http_request.call_args_list[0]
        self.assertIn("applications/schema", schema_call[0][1])


class TestTriggerSync(unittest.TestCase):
    """Tests for trigger_sync."""

    def _svc(self):
        return MagicMock()

    def test_sync_success(self):
        svc = self._svc()
        svc.http_request.return_value = (200, {}, "")
        trigger_sync(svc, "http://prowlarr", "key")
        svc.log.assert_called_once()
        self.assertIn("ApplicationIndexerSync", svc.log.call_args[0][0])

    def test_sync_201(self):
        svc = self._svc()
        svc.http_request.return_value = (201, {}, "")
        trigger_sync(svc, "http://prowlarr", "key")
        svc.log.assert_called_once()

    def test_sync_202(self):
        svc = self._svc()
        svc.http_request.return_value = (202, {}, "")
        trigger_sync(svc, "http://prowlarr", "key")
        svc.log.assert_called_once()

    def test_sync_failure_raises(self):
        svc = self._svc()
        svc.http_request.return_value = (500, None, "error")
        with self.assertRaises(RuntimeError) as ctx:
            trigger_sync(svc, "http://prowlarr", "key")
        self.assertIn("failed to trigger ApplicationIndexerSync", str(ctx.exception))

    def test_sync_sends_correct_payload(self):
        svc = self._svc()
        svc.http_request.return_value = (200, {}, "")
        trigger_sync(svc, "http://prowlarr", "key")
        call_args = svc.http_request.call_args
        self.assertEqual(call_args[1]["payload"], {"name": "ApplicationIndexerSync"})

    def test_sync_uses_post(self):
        svc = self._svc()
        svc.http_request.return_value = (200, {}, "")
        trigger_sync(svc, "http://prowlarr", "key")
        call_args = svc.http_request.call_args
        self.assertEqual(call_args[1]["method"], "POST")

    def test_sync_correct_endpoint(self):
        svc = self._svc()
        svc.http_request.return_value = (200, {}, "")
        trigger_sync(svc, "http://prowlarr", "key")
        call_args = svc.http_request.call_args
        self.assertEqual(call_args[0][1], "/api/v1/command")


if __name__ == "__main__":
    unittest.main()
