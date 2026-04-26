"""Tests for ControllerState architecture — actions, logs, config, thread safety."""

import sys
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ActionRecord, ActionStatus, ControllerState  # noqa: E402


class TestActionRecord(unittest.TestCase):
    def test_start_sets_running(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        self.assertEqual(r.status, ActionStatus.RUNNING)
        self.assertIsNotNone(r.started_at)

    def test_finish_success(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        r.finish()
        self.assertEqual(r.status, ActionStatus.COMPLETE)
        self.assertIsNotNone(r.completed_at)
        self.assertIsNone(r.error)

    def test_finish_error(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        r.finish(error="something broke")
        self.assertEqual(r.status, ActionStatus.ERROR)
        self.assertEqual(r.error, "something broke")

    def test_cancel(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        r.cancel()
        self.assertEqual(r.status, ActionStatus.CANCELLED)
        self.assertTrue(r.is_terminal)

    def test_timeout(self):
        r = ActionRecord(id="test-1", name="test", timeout_seconds=1)
        r.start()
        r.mark_timeout()
        self.assertEqual(r.status, ActionStatus.TIMEOUT)

    def test_elapsed_seconds(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        time.sleep(0.05)
        r.finish()
        self.assertGreater(r.elapsed_seconds, 0)

    def test_elapsed_none_before_start(self):
        r = ActionRecord(id="test-1", name="test")
        self.assertIsNone(r.elapsed_seconds)

    def test_is_terminal_false_when_running(self):
        r = ActionRecord(id="test-1", name="test")
        r.start()
        self.assertFalse(r.is_terminal)

    def test_to_dict(self):
        r = ActionRecord(id="test-1", name="test", triggered_by="admin")
        d = r.to_dict()
        self.assertEqual(d["id"], "test-1")
        self.assertEqual(d["triggered_by"], "admin")


class TestControllerStateActions(unittest.TestCase):
    def test_start_action(self):
        s = ControllerState()
        action = s.start_action("bootstrap")
        self.assertEqual(action.name, "bootstrap")
        self.assertEqual(action.status, ActionStatus.RUNNING)
        self.assertTrue(s.action_running)

    def test_finish_action(self):
        s = ControllerState()
        s.start_action("bootstrap")
        s.finish_action()
        self.assertFalse(s.action_running)
        self.assertEqual(len(s.action_history), 1)

    def test_cancel_action_returns_true(self):
        s = ControllerState()
        s.start_action("bootstrap")
        self.assertTrue(s.cancel_action())
        self.assertTrue(s.is_cancelled)

    def test_cancel_no_action_returns_false(self):
        s = ControllerState()
        self.assertFalse(s.cancel_action())

    def test_action_counter_increments(self):
        s = ControllerState()
        a1 = s.start_action("test")
        s.finish_action()
        a2 = s.start_action("test")
        self.assertNotEqual(a1.id, a2.id)

    def test_triggered_by_from_overrides(self):
        s = ControllerState()
        action = s.start_action("test", overrides={"_triggered_by": "admin", "foo": "bar"})
        self.assertEqual(action.triggered_by, "admin")
        self.assertNotIn("_triggered_by", action.overrides)


class TestControllerStateLogs(unittest.TestCase):
    def test_append_and_get(self):
        s = ControllerState()
        s.append_log("hello")
        logs = s.get_logs_since(0)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2], "hello")

    def test_filter_by_action(self):
        s = ControllerState()
        s.start_action("bootstrap")
        s.append_log("line1")
        s.finish_action()
        s.start_action("reconcile")
        s.append_log("line2")
        logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2], "line1")

    def test_concurrent_append(self):
        s = ControllerState()
        def writer(n):
            for i in range(50):
                s.append_log(f"thread-{n}-{i}")
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(s.get_logs_since(0)), 200)


class TestControllerStatePending(unittest.TestCase):
    def test_add_and_pop(self):
        s = ControllerState()
        s.add_pending("bootstrap", 10)
        self.assertEqual(len(s.pending_actions), 1)
        s.pop_pending("bootstrap")
        self.assertEqual(len(s.pending_actions), 0)

    def test_clear_pending(self):
        s = ControllerState()
        s.add_pending("a", 10)
        s.add_pending("b", 20)
        count = s.clear_pending()
        self.assertEqual(count, 2)
        self.assertEqual(len(s.pending_actions), 0)


class TestControllerStateConfig(unittest.TestCase):
    def test_set_and_get(self):
        s = ControllerState()
        s.set_config("key", "val")
        self.assertEqual(s.get_config("key"), "val")

    def test_get_default(self):
        s = ControllerState()
        self.assertEqual(s.get_config("missing", "default"), "default")

    def test_update_config(self):
        s = ControllerState()
        s.update_config({"a": 1, "b": 2})
        self.assertEqual(s.get_config("a"), 1)


class TestControllerStateFailedServices(unittest.TestCase):
    def test_mark_and_get(self):
        s = ControllerState()
        s.mark_service_failed("sonarr", "timeout")
        failed = s.get_failed_services()
        self.assertIn("sonarr", failed)

    def test_heal_removes(self):
        s = ControllerState()
        s.mark_service_failed("sonarr", "timeout")
        s.mark_service_healed("sonarr")
        self.assertEqual(len(s.get_failed_services()), 0)

    def test_to_dict_serialization(self):
        s = ControllerState()
        s.start_action("test")
        s.append_log("hello")
        d = s.to_dict()
        self.assertIn("phase", d)
        self.assertIn("current_action", d)


if __name__ == "__main__":
    unittest.main()
