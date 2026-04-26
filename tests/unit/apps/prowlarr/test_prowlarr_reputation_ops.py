"""Unit tests for Prowlarr reputation operations."""

import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.prowlarr.reputation_ops import (  # noqa: E402
    auto_add_tested_indexers,
    coerce_exclude_name_tokens,
    load_reputation_state,
    reputation_key,
    save_reputation_state,
    set_indexer_enabled,
)


class TestCoerceExcludeNameTokens(unittest.TestCase):
    """Tests for coerce_exclude_name_tokens."""

    def test_list_of_strings(self):
        result = coerce_exclude_name_tokens(["Foo", "Bar"])
        self.assertEqual(result, ["foo", "bar"])

    def test_list_with_whitespace(self):
        result = coerce_exclude_name_tokens(["  Foo  ", " Bar "])
        self.assertEqual(result, ["foo", "bar"])

    def test_list_with_empty_strings(self):
        result = coerce_exclude_name_tokens(["Foo", "", "  ", "Bar"])
        self.assertEqual(result, ["foo", "bar"])

    def test_list_with_ints(self):
        result = coerce_exclude_name_tokens([1, 2, 3])
        self.assertEqual(result, ["1", "2", "3"])

    def test_none_returns_empty(self):
        result = coerce_exclude_name_tokens(None)
        self.assertEqual(result, [])

    def test_empty_string(self):
        result = coerce_exclude_name_tokens("")
        self.assertEqual(result, [])

    def test_whitespace_only_string(self):
        result = coerce_exclude_name_tokens("   ")
        self.assertEqual(result, [])

    def test_comma_separated_string(self):
        result = coerce_exclude_name_tokens("foo,bar,baz")
        self.assertEqual(result, ["foo", "bar", "baz"])

    def test_comma_separated_with_spaces(self):
        result = coerce_exclude_name_tokens("  foo , bar , baz  ")
        self.assertEqual(result, ["foo", "bar", "baz"])

    def test_comma_separated_with_empty_segments(self):
        result = coerce_exclude_name_tokens("foo,,bar,,,baz")
        self.assertEqual(result, ["foo", "bar", "baz"])

    def test_single_value_string(self):
        result = coerce_exclude_name_tokens("hello")
        self.assertEqual(result, ["hello"])

    def test_numeric_string(self):
        result = coerce_exclude_name_tokens("123")
        self.assertEqual(result, ["123"])

    def test_empty_list(self):
        result = coerce_exclude_name_tokens([])
        self.assertEqual(result, [])

    def test_case_lowering(self):
        result = coerce_exclude_name_tokens("FOO,BAR")
        self.assertEqual(result, ["foo", "bar"])


class TestReputationKey(unittest.TestCase):
    """Tests for reputation_key."""

    def test_basic_key(self):
        self.assertEqual(reputation_key("Newznab", "MyIndexer"), "newznab::myindexer")

    def test_lowercase_input(self):
        self.assertEqual(reputation_key("torznab", "rarbg"), "torznab::rarbg")

    def test_mixed_case(self):
        self.assertEqual(reputation_key("Torznab", "NyaaFoo"), "torznab::nyaafoo")

    def test_empty_strings(self):
        self.assertEqual(reputation_key("", ""), "::")

    def test_special_characters(self):
        self.assertEqual(reputation_key("impl-1", "name_2"), "impl-1::name_2")


class TestLoadReputationState(unittest.TestCase):
    """Tests for load_reputation_state."""

    def test_file_not_exists(self, ):
        path = MagicMock(spec=Path)
        path.exists.return_value = False
        result = load_reputation_state(path)
        self.assertEqual(result, {"schema": 1, "indexers": {}})

    def test_valid_json(self, ):
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        state = {"schema": 1, "indexers": {"a::b": {"score": 5}}}
        path.read_text.return_value = json.dumps(state)
        result = load_reputation_state(path)
        self.assertEqual(result, state)

    def test_invalid_json(self):
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.read_text.return_value = "NOT JSON!!!"
        result = load_reputation_state(path)
        self.assertEqual(result, {"schema": 1, "indexers": {}})

    def test_json_is_list_not_dict(self):
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.read_text.return_value = json.dumps([1, 2, 3])
        result = load_reputation_state(path)
        self.assertEqual(result, {"schema": 1, "indexers": {}})

    def test_read_text_raises(self):
        path = MagicMock(spec=Path)
        path.exists.return_value = True
        path.read_text.side_effect = OSError("disk error")
        result = load_reputation_state(path)
        self.assertEqual(result, {"schema": 1, "indexers": {}})


class TestSaveReputationState(unittest.TestCase):
    """Tests for save_reputation_state."""

    def test_save_success(self):
        service = MagicMock()
        path = MagicMock(spec=Path)
        state = {"schema": 1, "indexers": {}}
        result = save_reputation_state(service, path, state)
        self.assertTrue(result)
        path.parent.mkdir.assert_called_once_with(parents=True, exist_ok=True)
        path.write_text.assert_called_once()
        self.assertIn("updated_at_epoch", state)

    def test_save_sets_epoch(self):
        service = MagicMock()
        path = MagicMock(spec=Path)
        state = {"schema": 1, "indexers": {}}
        before = int(time.time())
        save_reputation_state(service, path, state)
        after = int(time.time())
        self.assertGreaterEqual(state["updated_at_epoch"], before)
        self.assertLessEqual(state["updated_at_epoch"], after)

    def test_save_failure(self):
        service = MagicMock()
        path = MagicMock(spec=Path)
        path.parent.mkdir.side_effect = OSError("perm denied")
        state = {"schema": 1, "indexers": {}}
        result = save_reputation_state(service, path, state)
        self.assertFalse(result)
        service.log.assert_called_once()

    def test_save_writes_json(self):
        service = MagicMock()
        path = MagicMock(spec=Path)
        state = {"schema": 1, "indexers": {"foo::bar": {"score": 10}}}
        save_reputation_state(service, path, state)
        written = path.write_text.call_args[0][0]
        parsed = json.loads(written)
        self.assertEqual(parsed["schema"], 1)
        self.assertEqual(parsed["indexers"]["foo::bar"]["score"], 10)
        self.assertIn("updated_at_epoch", parsed)


class TestSetIndexerEnabled(unittest.TestCase):
    """Tests for set_indexer_enabled."""

    def test_enable_success(self):
        service = MagicMock()
        service.http_request.return_value = (200, {}, "")
        indexer = {"id": 5, "name": "Test", "enable": False}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertTrue(result)
        call_args = service.http_request.call_args
        self.assertEqual(call_args[1]["method"], "PUT")
        self.assertTrue(call_args[1]["payload"]["enable"])

    def test_disable_success(self):
        service = MagicMock()
        service.http_request.return_value = (200, {}, "")
        indexer = {"id": 5, "name": "Test", "enable": True}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, False)
        self.assertTrue(result)
        call_args = service.http_request.call_args
        self.assertFalse(call_args[1]["payload"]["enable"])

    def test_missing_id(self):
        service = MagicMock()
        indexer = {"name": "Test"}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertFalse(result)
        service.http_request.assert_not_called()

    def test_empty_id(self):
        service = MagicMock()
        indexer = {"id": "", "name": "Test"}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertFalse(result)
        service.http_request.assert_not_called()

    def test_none_id(self):
        service = MagicMock()
        indexer = {"id": None, "name": "Test"}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertFalse(result)

    def test_http_failure(self):
        service = MagicMock()
        service.http_request.return_value = (500, None, "Internal error")
        indexer = {"id": 7, "name": "BadIndexer"}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertFalse(result)
        service.log.assert_called_once()

    def test_http_201(self):
        service = MagicMock()
        service.http_request.return_value = (201, {}, "")
        indexer = {"id": 9, "name": "OkIndexer"}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertTrue(result)

    def test_http_202(self):
        service = MagicMock()
        service.http_request.return_value = (202, {}, "")
        indexer = {"id": 9, "name": "OkIndexer"}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertTrue(result)

    def test_http_404(self):
        service = MagicMock()
        service.http_request.return_value = (404, None, "Not found")
        indexer = {"id": 99, "name": "Missing"}
        result = set_indexer_enabled(service, "http://prowlarr", "key123", indexer, True)
        self.assertFalse(result)


class TestAutoAddTestedIndexers(unittest.TestCase):
    """Tests for auto_add_tested_indexers end-to-end behavior.

    The setUp here disables the curated-allowlist filter (added in
    v1.0.130; see ``contracts/curated-indexers.yaml``) so tests run
    against the historic "discover everything" path without depending
    on what slugs the YAML happens to contain. Tests that want to
    exercise the curated-filter path stop the patch and provide
    their own.
    """

    def setUp(self):
        super().setUp()
        self._curated_patch = patch(
            "media_stack.services.apps.prowlarr.reputation_ops."
            "ProwlarrReputationOps._load_curated_allowed_definitions",
            return_value=None,
        )
        self._curated_patch.start()
        self.addCleanup(self._curated_patch.stop)

    def _make_service(self):
        service = MagicMock()
        service.build_indexer_payload = lambda c: c
        return service

    def _schema_response(self, schemas):
        return (200, schemas, "")

    def _indexer_list_response(self, indexers):
        return (200, indexers, "")

    def test_schema_fetch_failure_raises(self):
        service = self._make_service()
        service.http_request.return_value = (500, None, "error")
        with self.assertRaises(RuntimeError):
            auto_add_tested_indexers(service, "http://prowlarr", "key123")

    def test_indexer_list_failure_raises(self):
        service = self._make_service()
        service.http_request.side_effect = [
            self._schema_response([]),
            (500, None, "error"),
        ]
        with self.assertRaises(RuntimeError):
            auto_add_tested_indexers(service, "http://prowlarr", "key123")

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    def test_no_candidates(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        service.http_request.side_effect = [
            self._schema_response([]),
            self._indexer_list_response([]),
        ]
        auto_add_tested_indexers(service, "http://prowlarr", "key123")
        service.log.assert_called()

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_successful_add(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "TestIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (200, {}, ""),   # test endpoint
            (201, {}, ""),   # create endpoint
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": True},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertTrue(len(add_logs) >= 1)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_skip_existing(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "Existing", "presets": []}
        existing = [{"implementation": "Newznab", "name": "Existing"}]
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response(existing),
        ]
        auto_add_tested_indexers(service, "http://prowlarr", "key123",
                                 reputation_cfg={"enabled": False})
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertEqual(len(add_logs), 0)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_exclude_tokens(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "PornIndexer", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            exclude_name_tokens=["porn"],
            reputation_cfg={"enabled": False},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertEqual(len(add_logs), 0)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_test_failure_skips_add(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "BadIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (500, None, "test failed"),  # test endpoint fails
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": False},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertEqual(len(add_logs), 0)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_create_failure_logged(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "FailCreate", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (200, {}, ""),   # test OK
            (500, None, "create error"),  # create fails
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": True},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        fail_logs = [l for l in logs if "[FAIL]" in l]
        self.assertTrue(len(fail_logs) >= 1)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_reputation_score_decreases_on_test_failure(self, mock_load, mock_save):
        state = {"schema": 1, "indexers": {}}
        mock_load.return_value = state
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "ScoreIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (500, None, "test failed"),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": True, "test_failure_score_delta": -4},
        )
        key = "newznab::scoreidx"
        self.assertIn(key, state["indexers"])
        self.assertEqual(state["indexers"][key]["score"], -4)
        self.assertEqual(state["indexers"][key]["failures"], 1)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_reputation_quarantine_on_bad_score(self, mock_load, mock_save):
        state = {
            "schema": 1,
            "indexers": {
                "newznab::badidx": {
                    "implementation": "Newznab",
                    "name": "BadIdx",
                    "score": -8,
                    "successes": 0,
                    "failures": 2,
                    "quarantined": False,
                    "quarantined_at_epoch": 0,
                },
            },
        }
        mock_load.return_value = state
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "BadIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (500, None, "test failed"),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={
                "enabled": True,
                "quarantine_score_threshold": -10,
                "quarantine_failure_threshold": 3,
                "test_failure_score_delta": -4,
            },
        )
        entry = state["indexers"]["newznab::badidx"]
        self.assertTrue(entry["quarantined"])
        self.assertGreater(entry["quarantined_at_epoch"], 0)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_quarantined_indexer_skipped(self, mock_load, mock_save):
        state = {
            "schema": 1,
            "indexers": {
                "newznab::quaridx": {
                    "implementation": "Newznab",
                    "name": "QuarIdx",
                    "score": -20,
                    "successes": 0,
                    "failures": 5,
                    "quarantined": True,
                    "quarantined_at_epoch": int(time.time()),
                },
            },
        }
        mock_load.return_value = state
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "QuarIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": True, "quarantine_ttl_hours": 72},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertEqual(len(add_logs), 0)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_quarantine_expired_retries(self, mock_load, mock_save):
        long_ago = int(time.time()) - (73 * 3600)
        state = {
            "schema": 1,
            "indexers": {
                "newznab::expidx": {
                    "implementation": "Newznab",
                    "name": "ExpIdx",
                    "score": -20,
                    "successes": 0,
                    "failures": 5,
                    "quarantined": True,
                    "quarantined_at_epoch": long_ago,
                },
            },
        }
        mock_load.return_value = state
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "ExpIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (200, {}, ""),   # test OK
            (201, {}, ""),   # create OK
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": True, "quarantine_ttl_hours": 72},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        retry_logs = [l for l in logs if "quarantine expired" in l]
        self.assertTrue(len(retry_logs) >= 1)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_presets_expanded(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {
            "implementation": "Parent",
            "name": "Parent",
            "presets": [
                {"implementation": "Newznab", "name": "Preset1"},
                {"implementation": "Newznab", "name": "Preset2"},
            ],
        }
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (200, {}, ""), (201, {}, ""),
            (200, {}, ""), (201, {}, ""),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": False},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertEqual(len(add_logs), 2)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_success_increases_reputation_score(self, mock_load, mock_save):
        state = {"schema": 1, "indexers": {}}
        mock_load.return_value = state
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "GoodIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (200, {}, ""),
            (201, {}, ""),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": True, "success_score_delta": 2},
        )
        key = "newznab::goodidx"
        self.assertEqual(state["indexers"][key]["score"], 2)
        self.assertEqual(state["indexers"][key]["successes"], 1)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_create_failure_decreases_score(self, mock_load, mock_save):
        state = {"schema": 1, "indexers": {}}
        mock_load.return_value = state
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "FailIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (200, {}, ""),
            (500, None, "create failed"),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": True, "create_failure_score_delta": -3},
        )
        key = "newznab::failidx"
        self.assertEqual(state["indexers"][key]["score"], -3)
        self.assertEqual(state["indexers"][key]["failures"], 1)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {
        "AUTO_INDEXER_PARALLEL_WORKERS": "1",
        "AUTO_INDEXER_EXCLUDE_NAME_TOKENS": "xxx",
    }, clear=False)
    def test_env_exclude_tokens_merged(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "XXXSite", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": False},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertEqual(len(add_logs), 0)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_candidate_without_impl_skipped(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"name": "NoImpl", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": False},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        summary = [l for l in logs if "summary" in l.lower()]
        self.assertTrue(any("scanned=0" in l for l in summary))

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_untested_fallback_when_enabled(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "FallbackIdx", "presets": []}
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
            (500, None, "test failed"),
            (201, {}, ""),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={
                "enabled": False,
                "allow_untested_fallback": True,
                "untested_fallback_max_add": 5,
            },
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertTrue(len(add_logs) >= 1)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_untested_fallback_respects_max_limit(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schemas = [
            {"implementation": "Newznab", "name": f"FB{i}", "presets": []}
            for i in range(5)
        ]
        responses = [
            self._schema_response(schemas),
            self._indexer_list_response([]),
        ]
        for _ in range(5):
            responses.append((500, None, "test failed"))
            responses.append((201, {}, ""))
        service.http_request.side_effect = responses
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={
                "enabled": False,
                "allow_untested_fallback": True,
                "untested_fallback_max_add": 2,
            },
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertLessEqual(len(add_logs), 2)

    def test_schema_returns_non_list(self):
        service = self._make_service()
        service.http_request.return_value = (200, "not a list", "")
        with self.assertRaises(RuntimeError):
            auto_add_tested_indexers(service, "http://prowlarr", "key123")

    def test_indexer_list_returns_non_list(self):
        service = self._make_service()
        service.http_request.side_effect = [
            (200, [{"implementation": "A", "name": "B", "presets": []}], ""),
            (200, "not a list", ""),
        ]
        with self.assertRaises(RuntimeError):
            auto_add_tested_indexers(service, "http://prowlarr", "key123")

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_reputation_disabled_no_state_saved(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        service.http_request.side_effect = [
            self._schema_response([]),
            self._indexer_list_response([]),
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": False},
        )
        mock_save.assert_not_called()

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_test_exception_treated_as_failure(self, mock_load, mock_save):
        mock_load.return_value = {"schema": 1, "indexers": {}}
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "ExcIdx", "presets": []}

        call_count = [0]
        original_responses = [
            self._schema_response([schema]),
            self._indexer_list_response([]),
        ]

        def side_effect(*args, **kwargs):
            if call_count[0] < len(original_responses):
                resp = original_responses[call_count[0]]
                call_count[0] += 1
                return resp
            call_count[0] += 1
            raise ConnectionError("network error")

        service.http_request.side_effect = side_effect
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={"enabled": False},
        )
        logs = [call[0][0] for call in service.log.call_args_list]
        add_logs = [l for l in logs if "[ADD]" in l]
        self.assertEqual(len(add_logs), 0)

    @patch("media_stack.services.apps.prowlarr.reputation_ops.save_reputation_state", return_value=True)
    @patch("media_stack.services.apps.prowlarr.reputation_ops.load_reputation_state")
    @patch.dict("os.environ", {"AUTO_INDEXER_PARALLEL_WORKERS": "1"}, clear=False)
    def test_quarantine_flags_indexer_on_repeated_failures(self, mock_load, mock_save):
        """Candidate not yet in Prowlarr but near quarantine threshold; one more
        test failure should push it over and set quarantined=True."""
        state = {
            "schema": 1,
            "indexers": {
                "newznab::badindexer": {
                    "implementation": "Newznab",
                    "name": "BadIndexer",
                    "score": -8,
                    "successes": 0,
                    "failures": 2,
                    "quarantined": False,
                    "quarantined_at_epoch": 0,
                },
            },
        }
        mock_load.return_value = state
        service = self._make_service()
        schema = {"implementation": "Newznab", "name": "BadIndexer", "presets": []}
        existing = []  # not yet in Prowlarr
        service.http_request.side_effect = [
            self._schema_response([schema]),
            self._indexer_list_response(existing),
            (500, None, "test failed"),  # test endpoint fails
        ]
        auto_add_tested_indexers(
            service, "http://prowlarr", "key123",
            reputation_cfg={
                "enabled": True,
                "quarantine_score_threshold": -10,
                "quarantine_failure_threshold": 3,
                "test_failure_score_delta": -4,
            },
        )
        entry = state["indexers"]["newznab::badindexer"]
        self.assertTrue(entry["quarantined"])
        self.assertEqual(entry["score"], -12)
        self.assertEqual(entry["failures"], 3)


if __name__ == "__main__":
    unittest.main()
