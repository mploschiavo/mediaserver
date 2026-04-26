"""Unit tests for MaintainerrRuleSyncService."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.maintainerr.rule_sync_service import (  # noqa: E402
    MaintainerrRuleSyncDependencies,
    MaintainerrRuleSyncService,
)


class TestMaintainerrRuleSyncService(unittest.TestCase):
    """Tests for MaintainerrRuleSyncService.sync_policy_rules."""

    def _make_service(self, request_fn=None, resolve_path_fn=None):
        self.log_calls = []
        deps = MaintainerrRuleSyncDependencies(
            log=lambda msg: self.log_calls.append(msg),
            request=request_fn or mock.Mock(return_value=(200, [], "")),
            resolve_path=resolve_path_fn or (lambda root, rel: Path(root) / rel),
        )
        return MaintainerrRuleSyncService(deps=deps)

    def _write_policy(self, tmp, policy_doc):
        policy_dir = Path(tmp) / "maintainerr"
        policy_dir.mkdir(parents=True, exist_ok=True)
        policy_path = policy_dir / "policy.json"
        policy_path.write_text(json.dumps(policy_doc), encoding="utf-8")
        return policy_path

    # -----------------------------------------------------------------------
    # Policy file missing / not found
    # -----------------------------------------------------------------------

    def test_skips_when_policy_file_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._make_service()
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("not found" in m for m in self.log_calls))

    def test_skips_when_policy_file_missing_logs_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._make_service()
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("policy.json" in m for m in self.log_calls))

    def test_skips_when_custom_policy_path_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._make_service()
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={"policy_relative_path": "custom/rules.json"},
                config_root=tmp,
            )
        self.assertTrue(any("not found" in m for m in self.log_calls))

    # -----------------------------------------------------------------------
    # Empty or invalid policy rules
    # -----------------------------------------------------------------------

    def test_skips_when_policy_rules_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, {"rules": []})
            svc = self._make_service()
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("no policy rules" in m for m in self.log_calls))

    def test_skips_when_policy_doc_has_no_rules_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, {"something": "else"})
            svc = self._make_service()
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("no policy rules" in m for m in self.log_calls))

    def test_raises_when_policy_rules_not_a_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, {"rules": "not-a-list"})
            svc = self._make_service()
            with self.assertRaises(RuntimeError) as ctx:
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )
            self.assertIn("must be a list", str(ctx.exception))

    def test_handles_empty_json_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_dir = Path(tmp) / "maintainerr"
            policy_dir.mkdir(parents=True)
            (policy_dir / "policy.json").write_text("", encoding="utf-8")
            svc = self._make_service()
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("no policy rules" in m for m in self.log_calls))

    # -----------------------------------------------------------------------
    # Library resolution failures
    # -----------------------------------------------------------------------

    def _policy_with_conditions(self, name="Test Rule"):
        return {
            "rules": [
                {
                    "name": name,
                    "conditions": {"watched": True},
                    "actions": {},
                }
            ]
        }

    def test_raises_when_no_libraries_available(self):
        """When the library endpoint returns an empty list, expect RuntimeError."""
        call_count = [0]

        def fake_request(url, path, **kw):
            call_count[0] += 1
            if "/api/media-server/libraries" in path:
                return (200, [], "")
            return (200, [], "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError) as ctx:
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )
            self.assertIn("no compatible", str(ctx.exception))

    def test_raises_when_library_request_fails(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (500, None, "Internal Server Error")
            return (200, [], "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError):
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )

    # -----------------------------------------------------------------------
    # Reading existing rules
    # -----------------------------------------------------------------------

    def _standard_request_fn(self, existing_rules=None, sync_status=201):
        """Return a request_fn that serves libraries, existing rules, and sync."""
        existing_rules = existing_rules or []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, existing_rules, "")
            if path == "/api/rules":
                return (sync_status, {"id": 99, "code": 1}, "")
            return (200, {}, "")

        return fake_request

    def test_raises_when_existing_rules_fetch_fails(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if "activeOnly" in path:
                return (500, None, "fail")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError) as ctx:
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )
            self.assertIn("failed reading existing rules", str(ctx.exception))

    def test_raises_when_existing_rules_not_list(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if "activeOnly" in path:
                return (200, {"not": "a list"}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError):
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )

    # -----------------------------------------------------------------------
    # Create vs update decisions
    # -----------------------------------------------------------------------

    def test_creates_new_rule_when_no_existing_match(self):
        calls = []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                calls.append(kw)
                return (201, {"id": 1, "code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("New Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("method"), "POST")

    def test_updates_existing_rule_when_name_matches(self):
        # Maintainerr's PUT /api/rules silently no-ops (v1.0.146);
        # the update path is DELETE old id + POST new payload.
        calls = []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [{"id": 42, "name": "Test Rule"}], "")
            if path == "/api/rules/42" and kw.get("method") == "DELETE":
                calls.append(("DELETE", path))
                return (204, None, "")
            if path == "/api/rules":
                calls.append((kw.get("method"), path))
                return (200, {"id": 99, "code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("Test Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertEqual(calls, [("DELETE", "/api/rules/42"), ("POST", "/api/rules")])

    def test_update_post_payload_omits_id(self):
        # After v1.0.146 the update path is DELETE-then-POST; the
        # POST payload must NOT carry the old id (Maintainerr would
        # treat it as a malformed create and reject), so the
        # service strips/never-injects ``id``.
        post_payloads = []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [{"id": 77, "name": "Test Rule"}], "")
            if path == "/api/rules/77" and kw.get("method") == "DELETE":
                return (204, None, "")
            if path == "/api/rules" and kw.get("method") == "POST":
                post_payloads.append(kw.get("payload"))
                return (200, {"id": 200, "code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("Test Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertEqual(len(post_payloads), 1)
        self.assertNotIn("id", post_payloads[0])

    def test_create_does_not_set_id_in_payload(self):
        payloads = []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                payloads.append(kw.get("payload"))
                return (201, {"id": 1, "code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("New Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertNotIn("id", payloads[0])

    # -----------------------------------------------------------------------
    # Sync failure handling
    # -----------------------------------------------------------------------

    def test_raises_when_sync_returns_http_error(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (500, None, "Internal Server Error")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError) as ctx:
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )
            self.assertIn("failed syncing rule", str(ctx.exception))

    def test_raises_when_sync_returns_code_zero(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (200, {"code": 0, "result": "bad field"}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError) as ctx:
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )
            self.assertIn("rule sync failed", str(ctx.exception))

    def test_raises_on_http_400(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (400, None, "Bad Request")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError):
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )

    # -----------------------------------------------------------------------
    # Logging summary
    # -----------------------------------------------------------------------

    def test_logs_created_count(self):
        req_fn = self._standard_request_fn(existing_rules=[])
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=req_fn)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        ok_logs = [m for m in self.log_calls if "created=1" in m]
        self.assertTrue(ok_logs)

    def test_logs_updated_count(self):
        req_fn = self._standard_request_fn(
            existing_rules=[{"id": 10, "name": "Test Rule"}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("Test Rule"))
            svc = self._make_service(request_fn=req_fn)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        ok_logs = [m for m in self.log_calls if "updated=1" in m]
        self.assertTrue(ok_logs)

    def test_logs_total_desired_count(self):
        req_fn = self._standard_request_fn()
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=req_fn)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        ok_logs = [m for m in self.log_calls if "total_desired=1" in m]
        self.assertTrue(ok_logs)

    # -----------------------------------------------------------------------
    # Multiple rules
    # -----------------------------------------------------------------------

    def test_creates_multiple_rules(self):
        calls = []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                calls.append(kw)
                return (201, {"code": 1}, "")
            return (200, {}, "")

        policy = {
            "rules": [
                {"name": "Rule A", "conditions": {"watched": True}, "actions": {}},
                {"name": "Rule B", "conditions": {"watched": True}, "actions": {}},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, policy)
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertEqual(len(calls), 2)

    def test_mix_create_and_update(self):
        # Updates → DELETE+POST; creates → POST only. Mixed batch
        # should produce exactly one DELETE and two POSTs (one
        # per rule, since POST is the only persisting verb).
        events = []

        def fake_request(url, path, **kw):
            method = kw.get("method")
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [{"id": 5, "name": "Rule A"}], "")
            if path.startswith("/api/rules/") and method == "DELETE":
                events.append(("DELETE", path))
                return (204, None, "")
            if path == "/api/rules" and method == "POST":
                events.append(("POST", path))
                return (200, {"code": 1}, "")
            return (200, {}, "")

        policy = {
            "rules": [
                {"name": "Rule A", "conditions": {"watched": True}, "actions": {}},
                {"name": "Rule B", "conditions": {"watched": True}, "actions": {}},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, policy)
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        delete_events = [e for e in events if e[0] == "DELETE"]
        post_events = [e for e in events if e[0] == "POST"]
        self.assertEqual(len(delete_events), 1, events)
        self.assertEqual(len(post_events), 2, events)

    def test_mix_create_update_logs_both_counts(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [{"id": 5, "name": "Rule A"}], "")
            if path == "/api/rules":
                return (200, {"code": 1}, "")
            return (200, {}, "")

        policy = {
            "rules": [
                {"name": "Rule A", "conditions": {"watched": True}, "actions": {}},
                {"name": "Rule B", "conditions": {"watched": True}, "actions": {}},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, policy)
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        ok_logs = [m for m in self.log_calls if "created=1" in m and "updated=1" in m]
        self.assertTrue(ok_logs)

    # -----------------------------------------------------------------------
    # Existing rules deduplication / indexing
    # -----------------------------------------------------------------------

    def test_existing_rules_skips_non_dict_items(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, ["not-a-dict", {"id": 1, "name": "Test Rule"}], "")
            if path == "/api/rules":
                return (200, {"code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("Test Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        ok_logs = [m for m in self.log_calls if "updated=1" in m]
        self.assertTrue(ok_logs)

    def test_existing_rules_skips_items_without_name(self):
        methods = []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [{"id": 1, "name": ""}, {"id": 2}], "")
            if path == "/api/rules":
                methods.append(kw.get("method"))
                return (201, {"code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("New Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertEqual(methods, ["POST"])

    # -----------------------------------------------------------------------
    # No translatable rules produced
    # -----------------------------------------------------------------------

    def test_warns_when_no_translatable_rules_produced(self):
        """When policy rules have no name, _desired_rule_payloads returns []."""

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            return (200, [], "")

        policy = {"rules": [{"conditions": {"watched": True}}]}  # no name
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, policy)
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("no translatable" in m for m in self.log_calls))

    # -----------------------------------------------------------------------
    # Existing rule with None id
    # -----------------------------------------------------------------------

    def test_existing_rule_with_none_id_uses_post(self):
        methods = []

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [{"name": "Test Rule"}], "")  # id is missing/None
            if path == "/api/rules":
                methods.append(kw.get("method"))
                return (201, {"code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("Test Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        # When existing has no id, it still matches by name but id is None so no PUT
        # Actually: existing_id = None => id is None => no PUT assignment
        # The code checks `if existing_id is not None` so method stays POST
        self.assertEqual(methods, ["POST"])

    # -----------------------------------------------------------------------
    # Name matching / whitespace
    # -----------------------------------------------------------------------

    def test_name_match_strips_whitespace(self):
        # Whitespace difference in remote name shouldn't prevent
        # update: matching takes the trimmed form, so "  Test Rule  "
        # in the remote registers as the existing rule. Result is
        # the standard DELETE+POST update path.
        events = []

        def fake_request(url, path, **kw):
            method = kw.get("method")
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [{"id": 1, "name": "  Test Rule  "}], "")
            if path.startswith("/api/rules/") and method == "DELETE":
                events.append(("DELETE", path))
                return (204, None, "")
            if path == "/api/rules" and method == "POST":
                events.append(("POST", path))
                return (200, {"code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions("Test Rule"))
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertEqual(events, [("DELETE", "/api/rules/1"), ("POST", "/api/rules")])

    # -----------------------------------------------------------------------
    # Dependencies dataclass
    # -----------------------------------------------------------------------

    def test_deps_dataclass_fields(self):
        deps = MaintainerrRuleSyncDependencies(
            log=lambda m: None,
            request=lambda *a, **kw: (200, {}, ""),
            resolve_path=lambda r, p: Path(r) / p,
        )
        self.assertTrue(callable(deps.log))
        self.assertTrue(callable(deps.request))
        self.assertTrue(callable(deps.resolve_path))

    # -----------------------------------------------------------------------
    # Translator wiring
    # -----------------------------------------------------------------------

    def test_translator_returns_translation_service(self):
        svc = self._make_service()
        translator = svc._translator()
        self.assertTrue(hasattr(translator, "_resolve_libraries"))

    def test_translator_shares_deps(self):
        svc = self._make_service()
        translator = svc._translator()
        # The translator's log should be the same function
        self.assertIs(translator.deps.log, svc.deps.log)

    # -----------------------------------------------------------------------
    # Policy relative path config
    # -----------------------------------------------------------------------

    def test_uses_custom_policy_relative_path(self):
        resolve_calls = []

        def tracking_resolve(root, rel):
            resolve_calls.append(rel)
            return Path(root) / rel

        with tempfile.TemporaryDirectory() as tmp:
            custom_dir = Path(tmp) / "custom"
            custom_dir.mkdir()
            (custom_dir / "rules.json").write_text(
                json.dumps({"rules": []}), encoding="utf-8"
            )
            svc = self._make_service(resolve_path_fn=tracking_resolve)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={"policy_relative_path": "custom/rules.json"},
                config_root=tmp,
            )
        self.assertEqual(resolve_calls[0], "custom/rules.json")

    def test_default_policy_relative_path(self):
        resolve_calls = []

        def tracking_resolve(root, rel):
            resolve_calls.append(rel)
            return Path(root) / rel

        with tempfile.TemporaryDirectory() as tmp:
            svc = self._make_service(resolve_path_fn=tracking_resolve)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertEqual(resolve_calls[0], "maintainerr/policy.json")

    # -----------------------------------------------------------------------
    # Edge cases: code field in response
    # -----------------------------------------------------------------------

    def test_success_when_response_code_is_1(self):
        """code=1 means success; should not raise."""
        req_fn = self._standard_request_fn()
        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=req_fn)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("[OK]" in m for m in self.log_calls))

    def test_non_dict_response_does_not_trigger_code_check(self):
        """When response data is a list (not dict), code check is skipped."""

        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (201, [1, 2, 3], "")  # list, not dict
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("[OK]" in m for m in self.log_calls))

    # -----------------------------------------------------------------------
    # HTTP status boundary tests
    # -----------------------------------------------------------------------

    def test_http_199_raises(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (199, {}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError):
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )

    def test_http_300_raises(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (300, {}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            with self.assertRaises(RuntimeError):
                svc.sync_policy_rules(
                    maintainerr_url="http://m:6246",
                    maintainerr_cfg={},
                    config_root=tmp,
                )

    def test_http_200_succeeds(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (200, {"code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("[OK]" in m for m in self.log_calls))

    def test_http_299_succeeds(self):
        def fake_request(url, path, **kw):
            if "/api/media-server/libraries" in path:
                return (200, [{"id": "1", "title": "Movies", "type": "movie"}], "")
            if path == "/api/rules?activeOnly=false":
                return (200, [], "")
            if path == "/api/rules":
                return (299, {"code": 1}, "")
            return (200, {}, "")

        with tempfile.TemporaryDirectory() as tmp:
            self._write_policy(tmp, self._policy_with_conditions())
            svc = self._make_service(request_fn=fake_request)
            svc.sync_policy_rules(
                maintainerr_url="http://m:6246",
                maintainerr_cfg={},
                config_root=tmp,
            )
        self.assertTrue(any("[OK]" in m for m in self.log_calls))


if __name__ == "__main__":
    unittest.main()
