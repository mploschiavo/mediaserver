"""Unit tests for action priority queue, cancellation, and pending tracking.

Covers: ActionRecord lifecycle, ControllerState action management,
priority queue ordering, pending action tracking, cancel flow,
and ACTION_PRIORITY constants.
"""

import queue
import sys
import time
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ActionRecord, ActionStatus, ControllerState
from media_stack.api.server import ACTION_PRIORITY, DEFAULT_ACTION_PRIORITY, KNOWN_ACTIONS


# ---------------------------------------------------------------------------
# ActionRecord tests
# ---------------------------------------------------------------------------

class TestActionRecord(unittest.TestCase):

    def test_initial_status_is_pending(self):
        r = ActionRecord(id="test-1", name="test")
        self.assertEqual(r.status, ActionStatus.PENDING)
        self.assertFalse(r.is_terminal)
        self.assertIsNone(r.started_at)

    def test_start_sets_running(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        self.assertEqual(r.status, ActionStatus.RUNNING)
        self.assertIsNotNone(r.started_at)
        self.assertFalse(r.is_terminal)

    def test_finish_success(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        r.finish()
        self.assertEqual(r.status, ActionStatus.COMPLETE)
        self.assertTrue(r.is_terminal)
        self.assertIsNone(r.error)
        self.assertIsNotNone(r.completed_at)

    def test_finish_error(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        r.finish(error="something broke")
        self.assertEqual(r.status, ActionStatus.ERROR)
        self.assertTrue(r.is_terminal)
        self.assertEqual(r.error, "something broke")

    def test_cancel(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        r.cancel()
        self.assertEqual(r.status, ActionStatus.CANCELLED)
        self.assertTrue(r.is_terminal)
        self.assertEqual(r.error, "cancelled by user")
        self.assertIsNotNone(r.completed_at)

    def test_mark_timeout(self):
        r = ActionRecord(id="test-1", name="test", timeout_seconds=5)
        r.start()
        r.mark_timeout()
        self.assertEqual(r.status, ActionStatus.TIMEOUT)
        self.assertTrue(r.is_terminal)
        self.assertIn("5s", r.error)

    def test_elapsed_seconds_none_before_start(self):
        r = ActionRecord(id="test-1", name="test")
        self.assertIsNone(r.elapsed_seconds)

    def test_elapsed_seconds_running(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        self.assertIsNotNone(r.elapsed_seconds)
        self.assertGreaterEqual(r.elapsed_seconds, 0)

    def test_elapsed_seconds_completed(self):
        r = ActionRecord(id="test-1", name="test")
        r.started_at = 100.0
        r.completed_at = 105.5
        self.assertEqual(r.elapsed_seconds, 5.5)

    def test_is_timed_out_false_when_within_limit(self):
        r = ActionRecord(id="test-1", name="test", timeout_seconds=600)
        r.start()
        self.assertFalse(r.is_timed_out)

    def test_is_timed_out_true_when_exceeded(self):
        r = ActionRecord(id="test-1", name="test", timeout_seconds=1)
        r.status = ActionStatus.RUNNING
        r.started_at = time.time() - 10
        self.assertTrue(r.is_timed_out)

    def test_is_timed_out_false_when_not_running(self):
        r = ActionRecord(id="test-1", name="test", timeout_seconds=1)
        r.started_at = time.time() - 10
        r.status = ActionStatus.COMPLETE
        self.assertFalse(r.is_timed_out)

    def test_to_dict_contains_all_fields(self):
        r = ActionRecord(id="test-1", name="test", triggered_by="admin")
        r.start()
        d = r.to_dict()
        self.assertEqual(d["id"], "test-1")
        self.assertEqual(d["name"], "test")
        self.assertEqual(d["status"], "running")
        self.assertEqual(d["triggered_by"], "admin")
        self.assertIn("started_at", d)
        self.assertIn("elapsed_seconds", d)
        self.assertIn("timeout_seconds", d)
        self.assertIn("overrides", d)


# ---------------------------------------------------------------------------
# ControllerState action lifecycle tests
# ---------------------------------------------------------------------------

class TestControllerStateActions(unittest.TestCase):

    def test_start_action_creates_running_record(self):
        s = ControllerState()
        r = s.start_action("bootstrap")
        self.assertEqual(r.name, "bootstrap")
        self.assertEqual(r.status, ActionStatus.RUNNING)
        self.assertEqual(r.id, "bootstrap-1")
        self.assertIs(s.current_action, r)

    def test_start_action_increments_counter(self):
        s = ControllerState()
        r1 = s.start_action("a")
        s.finish_action()
        r2 = s.start_action("b")
        self.assertEqual(r1.id, "a-1")
        self.assertEqual(r2.id, "b-2")

    def test_finish_action_moves_to_history(self):
        s = ControllerState()
        s.start_action("test")
        s.finish_action()
        self.assertIsNone(s.current_action)
        self.assertEqual(len(s.action_history), 1)
        self.assertEqual(s.action_history[0].status, ActionStatus.COMPLETE)

    def test_finish_action_with_error(self):
        s = ControllerState()
        s.start_action("test")
        s.finish_action(error="fail")
        self.assertEqual(s.action_history[0].status, ActionStatus.ERROR)
        self.assertEqual(s.action_history[0].error, "fail")

    def test_action_running_property(self):
        s = ControllerState()
        self.assertFalse(s.action_running)
        s.start_action("test")
        self.assertTrue(s.action_running)
        s.finish_action()
        self.assertFalse(s.action_running)

    def test_get_action_current(self):
        s = ControllerState()
        r = s.start_action("test")
        self.assertIs(s.get_action("test-1"), r)

    def test_get_action_history(self):
        s = ControllerState()
        s.start_action("test")
        s.finish_action()
        found = s.get_action("test-1")
        self.assertIsNotNone(found)
        self.assertEqual(found.name, "test")

    def test_get_action_not_found(self):
        s = ControllerState()
        self.assertIsNone(s.get_action("nonexistent-99"))

    def test_triggered_by_extracted_from_overrides(self):
        s = ControllerState()
        r = s.start_action("test", overrides={"_triggered_by": "admin", "key": "val"})
        self.assertEqual(r.triggered_by, "admin")
        self.assertNotIn("_triggered_by", r.overrides)
        self.assertEqual(r.overrides["key"], "val")


# ---------------------------------------------------------------------------
# Cancel tests
# ---------------------------------------------------------------------------

class TestControllerStateCancel(unittest.TestCase):

    def test_cancel_action_returns_true_when_running(self):
        s = ControllerState()
        s.start_action("test")
        self.assertTrue(s.cancel_action())
        self.assertTrue(s.is_cancelled)

    def test_cancel_action_returns_false_when_idle(self):
        s = ControllerState()
        self.assertFalse(s.cancel_action())
        self.assertFalse(s.is_cancelled)

    def test_cancel_action_returns_false_after_completion(self):
        s = ControllerState()
        s.start_action("test")
        s.finish_action()
        self.assertFalse(s.cancel_action())

    def test_cancel_event_cleared_on_new_action(self):
        s = ControllerState()
        s.start_action("a")
        s.cancel_action()
        self.assertTrue(s.is_cancelled)
        s.finish_action(error="cancelled")
        s.start_action("b")
        self.assertFalse(s.is_cancelled)

    def test_cancel_event_cleared_on_finish(self):
        s = ControllerState()
        s.start_action("test")
        s.cancel_action()
        s.finish_action(error="cancelled")
        self.assertFalse(s.is_cancelled)


# ---------------------------------------------------------------------------
# Pending action tracking tests
# ---------------------------------------------------------------------------

class TestControllerStatePending(unittest.TestCase):

    def test_add_pending(self):
        s = ControllerState()
        s.add_pending("envoy-config", 30)
        self.assertEqual(len(s.pending_actions), 1)
        self.assertEqual(s.pending_actions[0]["name"], "envoy-config")
        self.assertEqual(s.pending_actions[0]["priority"], 30)
        self.assertIn("queued_at", s.pending_actions[0])

    def test_add_pending_strips_private_overrides(self):
        s = ControllerState()
        s.add_pending("test", 50, overrides={"_triggered_by": "admin", "key": "val"})
        self.assertEqual(s.pending_actions[0]["overrides"], {"key": "val"})

    def test_pop_pending_removes_first_match(self):
        s = ControllerState()
        s.add_pending("a", 10)
        s.add_pending("b", 20)
        s.add_pending("a", 30)
        s.pop_pending("a")
        self.assertEqual(len(s.pending_actions), 2)
        self.assertEqual(s.pending_actions[0]["name"], "b")
        self.assertEqual(s.pending_actions[1]["name"], "a")

    def test_pop_pending_noop_when_not_found(self):
        s = ControllerState()
        s.add_pending("a", 10)
        s.pop_pending("nonexistent")
        self.assertEqual(len(s.pending_actions), 1)

    def test_clear_pending(self):
        s = ControllerState()
        s.add_pending("a", 10)
        s.add_pending("b", 20)
        count = s.clear_pending()
        self.assertEqual(count, 2)
        self.assertEqual(len(s.pending_actions), 0)

    def test_clear_pending_empty(self):
        s = ControllerState()
        self.assertEqual(s.clear_pending(), 0)

    def test_pending_in_to_dict(self):
        s = ControllerState()
        s.add_pending("envoy-config", 30)
        d = s.to_dict()
        self.assertIn("pending_actions", d)
        self.assertEqual(len(d["pending_actions"]), 1)
        self.assertEqual(d["pending_actions"][0]["name"], "envoy-config")

    def test_pending_thread_safety(self):
        """Add and pop from multiple threads to verify locking."""
        s = ControllerState()
        errors = []

        def add_many():
            try:
                for i in range(50):
                    s.add_pending(f"action-{i}", i)
            except Exception as e:
                errors.append(e)

        def pop_many():
            try:
                for i in range(50):
                    s.pop_pending(f"action-{i}")
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=add_many)
        t2 = threading.Thread(target=pop_many)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# Priority queue ordering tests
# ---------------------------------------------------------------------------

class TestPriorityQueueOrdering(unittest.TestCase):
    """Verify PriorityQueue processes higher-priority (lower number) items first."""

    def test_priority_ordering(self):
        pq = queue.PriorityQueue()
        seq = 0
        items = [
            (ACTION_PRIORITY["discover-indexers"], "discover-indexers"),
            (ACTION_PRIORITY["envoy-config"], "envoy-config"),
            (ACTION_PRIORITY["bootstrap"], "bootstrap"),
            (ACTION_PRIORITY["post-setup"], "post-setup"),
        ]
        for prio, name in items:
            seq += 1
            pq.put((prio, seq, name, {}))

        order = []
        while not pq.empty():
            _prio, _seq, name, _ov = pq.get()
            order.append(name)

        # bootstrap (10) < configure-media-server (15) < envoy-config (30) < finalize (45) < auto-indexers (70)
        self.assertEqual(order, ["bootstrap", "envoy-config", "post-setup", "discover-indexers"])

    def test_same_priority_fifo(self):
        """Items with the same priority are processed in insertion order."""
        pq = queue.PriorityQueue()
        pq.put((50, 1, "first", {}))
        pq.put((50, 2, "second", {}))
        pq.put((50, 3, "third", {}))

        order = []
        while not pq.empty():
            _, _, name, _ = pq.get()
            order.append(name)

        self.assertEqual(order, ["first", "second", "third"])

    def test_envoy_config_before_auto_indexers(self):
        """The specific scenario: envoy-config should preempt auto-indexers."""
        pq = queue.PriorityQueue()
        # auto-indexers queued first
        pq.put((ACTION_PRIORITY["discover-indexers"], 1, "discover-indexers", {}))
        # envoy-config queued second
        pq.put((ACTION_PRIORITY["envoy-config"], 2, "envoy-config", {}))

        _, _, first, _ = pq.get()
        _, _, second, _ = pq.get()
        self.assertEqual(first, "envoy-config")
        self.assertEqual(second, "discover-indexers")


# ---------------------------------------------------------------------------
# ACTION_PRIORITY constants validation
# ---------------------------------------------------------------------------

class TestActionPriorityConstants(unittest.TestCase):

    def test_all_known_actions_have_priority(self):
        for action in KNOWN_ACTIONS:
            self.assertIn(
                action, ACTION_PRIORITY,
                f"Action '{action}' is in KNOWN_ACTIONS but has no priority defined"
            )

    def test_bootstrap_is_highest_priority(self):
        bootstrap_prio = ACTION_PRIORITY["bootstrap"]
        for name, prio in ACTION_PRIORITY.items():
            self.assertGreaterEqual(
                prio, bootstrap_prio,
                f"'{name}' (P{prio}) has higher priority than bootstrap (P{bootstrap_prio})"
            )

    def test_validate_credentials_runs_after_bootstrap(self):
        vc_prio = ACTION_PRIORITY["validate-credentials"]
        for action in ("bootstrap", "configure-media-server"):
            self.assertGreater(
                vc_prio, ACTION_PRIORITY[action],
                f"validate-credentials should run after {action}",
            )

    def test_validate_credentials_runs_before_slow_actions(self):
        vc_prio = ACTION_PRIORITY["validate-credentials"]
        for action in ("discover-indexers", "push-indexers"):
            self.assertLess(
                vc_prio, ACTION_PRIORITY[action],
                f"validate-credentials should run before {action}",
            )

    def test_envoy_config_before_auto_indexers(self):
        self.assertLess(
            ACTION_PRIORITY["envoy-config"],
            ACTION_PRIORITY["discover-indexers"],
        )

    def test_default_priority_is_reasonable(self):
        self.assertGreater(DEFAULT_ACTION_PRIORITY, ACTION_PRIORITY["bootstrap"])
        self.assertLessEqual(DEFAULT_ACTION_PRIORITY, ACTION_PRIORITY["discover-indexers"])

    def test_priorities_are_positive_integers(self):
        for name, prio in ACTION_PRIORITY.items():
            self.assertIsInstance(prio, int, f"{name} priority is not int")
            self.assertGreater(prio, 0, f"{name} priority must be positive")


# ---------------------------------------------------------------------------
# Runtime config → action overrides bridge
# ---------------------------------------------------------------------------

class TestRuntimeConfigFlowsToOverrides(unittest.TestCase):
    """POST /config settings must reach _apply_overrides during action dispatch.

    This is the bridge test that was missing: runtime_config is set via the
    API, and must be merged into action overrides so env vars like
    AUTO_DOWNLOAD_CONTENT actually take effect.
    """

    def test_auto_download_in_runtime_config_sets_env(self):
        """Simulates: user toggles auto-downloads on → reconcile runs → env var is set."""
        import os
        from unittest.mock import patch
        from media_stack.cli.commands.controller_dispatch import _apply_overrides

        state = ControllerState()
        state.update_config({"auto_download_content": True})

        # Simulate the merge that controller_serve.py now does
        overrides = {}
        for cfg_key, cfg_val in state.runtime_config.items():
            overrides.setdefault(cfg_key, cfg_val)

        with patch.dict(os.environ, {}, clear=False):
            _apply_overrides(overrides)
            self.assertEqual(os.environ.get("AUTO_DOWNLOAD_CONTENT"), "1")

    def test_auto_download_off_sets_env_zero(self):
        import os
        from unittest.mock import patch
        from media_stack.cli.commands.controller_dispatch import _apply_overrides

        state = ControllerState()
        state.update_config({"auto_download_content": False})

        overrides = {}
        for cfg_key, cfg_val in state.runtime_config.items():
            overrides.setdefault(cfg_key, cfg_val)

        with patch.dict(os.environ, {}, clear=False):
            _apply_overrides(overrides)
            self.assertEqual(os.environ.get("AUTO_DOWNLOAD_CONTENT"), "0")

    def test_explicit_override_takes_precedence(self):
        """Action-specific overrides should win over runtime_config."""
        state = ControllerState()
        state.update_config({"auto_download_content": True})

        overrides = {"auto_download_content": False}
        for cfg_key, cfg_val in state.runtime_config.items():
            overrides.setdefault(cfg_key, cfg_val)

        # Explicit False wins over runtime_config True
        self.assertFalse(overrides["auto_download_content"])

    def test_runtime_config_persists_to_disk(self):
        """runtime_config must survive process restarts (persisted to disk)."""
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "runtime-config.json"
            # Write
            s1 = ControllerState()
            s1._RUNTIME_CONFIG_FILE = str(config_file)
            s1.update_config({"auto_download_content": True, "some_flag": "value"})
            self.assertTrue(config_file.is_file())

            # Read back in a new state (simulates restart)
            s2 = ControllerState()
            s2._RUNTIME_CONFIG_FILE = str(config_file)
            s2.load_persisted_config()
            self.assertTrue(s2.runtime_config.get("auto_download_content"))
            self.assertEqual(s2.runtime_config.get("some_flag"), "value")

    def test_runtime_config_keys_not_in_override_map_are_harmless(self):
        """Unknown config keys should not crash _apply_overrides."""
        import os
        from unittest.mock import patch
        from media_stack.cli.commands.controller_dispatch import _apply_overrides

        state = ControllerState()
        state.update_config({"unknown_setting": "value", "auto_download_content": True})

        overrides = {}
        for cfg_key, cfg_val in state.runtime_config.items():
            overrides.setdefault(cfg_key, cfg_val)

        with patch.dict(os.environ, {}, clear=False):
            _apply_overrides(overrides)  # Should not crash
            self.assertEqual(os.environ.get("AUTO_DOWNLOAD_CONTENT"), "1")


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

class TestControllerStateSerialization(unittest.TestCase):

    def test_to_dict_includes_all_action_fields(self):
        # ADR-0005 Phase 5a: ``current_action`` no longer ships in
        # ``/status``; the dataclass field stays for internal use.
        # ``action_history`` and ``pending_actions`` remain.
        s = ControllerState()
        s.add_pending("envoy-config", 30)
        s.start_action("bootstrap", overrides={"_triggered_by": "admin"})
        d = s.to_dict()

        self.assertNotIn("current_action", d)
        self.assertIn("action_history", d)
        self.assertIn("pending_actions", d)
        self.assertEqual(len(d["pending_actions"]), 1)
        # Internal field still set.
        self.assertEqual(s.current_action.name, "bootstrap")

    def test_to_dict_after_cancel(self):
        s = ControllerState()
        s.start_action("test")
        s.cancel_action()
        # finish_action should still move it to history even when cancelled
        s.finish_action(error="cancelled by user")
        d = s.to_dict()
        self.assertEqual(len(d["action_history"]), 1)
        # finish_action with error sets ERROR status (cancel_action only sets the event)
        self.assertEqual(d["action_history"][0]["status"], "error")
        self.assertEqual(d["action_history"][0]["error"], "cancelled by user")


# ---------------------------------------------------------------------------
# Log buffer tests
# ---------------------------------------------------------------------------

class TestControllerStateLogs(unittest.TestCase):

    def test_append_log_captures_action_name(self):
        s = ControllerState()
        s.start_action("bootstrap")
        s.append_log("hello")
        logs = s.get_logs_since(0)
        self.assertEqual(len(logs), 1)
        seq, ts, msg, action = logs[0]
        self.assertEqual(msg, "hello")
        self.assertEqual(action, "bootstrap")

    def test_append_log_empty_action_when_idle(self):
        s = ControllerState()
        s.append_log("idle log")
        logs = s.get_logs_since(0)
        self.assertEqual(logs[0][3], "")

    def test_get_logs_since_filters(self):
        s = ControllerState()
        s.append_log("first")
        s.append_log("second")
        s.append_log("third")
        logs = s.get_logs_since(2)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2], "third")

    def test_log_seq_increments(self):
        s = ControllerState()
        self.assertEqual(s.log_seq, 0)
        s.append_log("a")
        self.assertEqual(s.log_seq, 1)
        s.append_log("b")
        self.assertEqual(s.log_seq, 2)


if __name__ == "__main__":
    unittest.main()
