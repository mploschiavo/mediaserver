"""Tests for log filtering by action in state.get_logs_since()."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ControllerState  # noqa: E402


class TestLogFilteringByAction(unittest.TestCase):
    """get_logs_since should filter by action when specified."""

    def test_filter_returns_matching_action_only(self):
        s = ControllerState()
        s.start_action("bootstrap")
        s.append_log("boot msg 1")
        s.append_log("boot msg 2")
        s.finish_action()
        s.start_action("reconcile")
        s.append_log("recon msg")
        s.finish_action()
        logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(l[3] == "bootstrap" for l in logs))

    def test_filter_empty_string_returns_all(self):
        s = ControllerState()
        s.start_action("bootstrap")
        s.append_log("boot")
        s.finish_action()
        s.start_action("reconcile")
        s.append_log("recon")
        s.finish_action()
        logs = s.get_logs_since(0, action="")
        self.assertEqual(len(logs), 2)

    def test_filter_nonexistent_action_returns_empty(self):
        s = ControllerState()
        s.start_action("bootstrap")
        s.append_log("msg")
        s.finish_action()
        logs = s.get_logs_since(0, action="nonexistent")
        self.assertEqual(len(logs), 0)

    def test_filter_respects_after_seq(self):
        s = ControllerState()
        s.start_action("bootstrap")
        s.append_log("msg1")
        s.append_log("msg2")
        s.append_log("msg3")
        s.finish_action()
        all_logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(all_logs), 3)
        seq_after_first = all_logs[0][0]
        filtered = s.get_logs_since(seq_after_first, action="bootstrap")
        self.assertEqual(len(filtered), 2)

    def test_no_action_defaults_to_all(self):
        s = ControllerState()
        s.start_action("a")
        s.append_log("1")
        s.finish_action()
        s.start_action("b")
        s.append_log("2")
        s.finish_action()
        logs = s.get_logs_since(0)
        self.assertEqual(len(logs), 2)

    def test_multiple_actions_filter_each(self):
        s = ControllerState()
        for action_name in ["bootstrap", "finalize", "auto-indexers"]:
            s.start_action(action_name)
            s.append_log(f"{action_name} log")
            s.finish_action()
        for action_name in ["bootstrap", "finalize", "auto-indexers"]:
            logs = s.get_logs_since(0, action=action_name)
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0][2], f"{action_name} log")

    def test_filter_with_no_logs_returns_empty(self):
        s = ControllerState()
        logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(logs), 0)

    def test_logs_without_action_context(self):
        s = ControllerState()
        s.append_log("orphan log")
        logs = s.get_logs_since(0, action="")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][3], "")

    def test_filter_action_excludes_orphan_logs(self):
        s = ControllerState()
        s.append_log("orphan")
        s.start_action("bootstrap")
        s.append_log("tagged")
        s.finish_action()
        logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2], "tagged")

    def test_log_entries_have_correct_structure(self):
        s = ControllerState()
        s.start_action("test-action")
        s.append_log("test message")
        s.finish_action()
        logs = s.get_logs_since(0, action="test-action")
        self.assertEqual(len(logs), 1)
        seq, ts, msg, action = logs[0]
        self.assertIsInstance(seq, int)
        self.assertIsInstance(ts, float)
        self.assertEqual(msg, "test message")
        self.assertEqual(action, "test-action")


if __name__ == "__main__":
    unittest.main()
