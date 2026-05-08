"""Tests for log filtering by action in state.get_logs_since().

ADR-0005 Phase 5c.4c: the per-line action tag is now sourced from
``runtime_platform.current_action_tag`` (a ``contextvars.ContextVar``
context-manager), not the retired ``ControllerState.current_action``
field. The SSE filter shape and ``get_logs_since(action=...)``
semantics are unchanged — these tests bind the tag with the new
context-manager idiom.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ControllerState  # noqa: E402
from media_stack.services.runtime_platform import current_action_tag  # noqa: E402


class TestLogFilteringByAction(unittest.TestCase):
    """get_logs_since should filter by action when specified."""

    def test_filter_returns_matching_action_only(self):
        s = ControllerState()
        with current_action_tag("bootstrap"):
            s.append_log("boot msg 1")
            s.append_log("boot msg 2")
        with current_action_tag("reconcile"):
            s.append_log("recon msg")
        logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(l[3] == "bootstrap" for l in logs))

    def test_filter_empty_string_returns_all(self):
        s = ControllerState()
        with current_action_tag("bootstrap"):
            s.append_log("boot")
        with current_action_tag("reconcile"):
            s.append_log("recon")
        logs = s.get_logs_since(0, action="")
        self.assertEqual(len(logs), 2)

    def test_filter_nonexistent_action_returns_empty(self):
        s = ControllerState()
        with current_action_tag("bootstrap"):
            s.append_log("msg")
        logs = s.get_logs_since(0, action="nonexistent")
        self.assertEqual(len(logs), 0)

    def test_filter_respects_after_seq(self):
        s = ControllerState()
        with current_action_tag("bootstrap"):
            s.append_log("msg1")
            s.append_log("msg2")
            s.append_log("msg3")
        all_logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(all_logs), 3)
        seq_after_first = all_logs[0][0]
        filtered = s.get_logs_since(seq_after_first, action="bootstrap")
        self.assertEqual(len(filtered), 2)

    def test_no_action_defaults_to_all(self):
        s = ControllerState()
        with current_action_tag("a"):
            s.append_log("1")
        with current_action_tag("b"):
            s.append_log("2")
        logs = s.get_logs_since(0)
        self.assertEqual(len(logs), 2)

    def test_multiple_actions_filter_each(self):
        s = ControllerState()
        for action_name in ["bootstrap", "post-setup", "discover-indexers"]:
            with current_action_tag(action_name):
                s.append_log(f"{action_name} log")
        for action_name in ["bootstrap", "post-setup", "discover-indexers"]:
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
        with current_action_tag("bootstrap"):
            s.append_log("tagged")
        logs = s.get_logs_since(0, action="bootstrap")
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2], "tagged")

    def test_log_entries_have_correct_structure(self):
        s = ControllerState()
        with current_action_tag("test-action"):
            s.append_log("test message")
        logs = s.get_logs_since(0, action="test-action")
        self.assertEqual(len(logs), 1)
        seq, ts, msg, action = logs[0]
        self.assertIsInstance(seq, int)
        self.assertIsInstance(ts, float)
        self.assertEqual(msg, "test message")
        self.assertEqual(action, "test-action")


if __name__ == "__main__":
    unittest.main()
