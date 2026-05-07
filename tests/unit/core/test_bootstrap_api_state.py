"""Unit tests for media_stack.api.state.BootstrapState."""

import time
import unittest

from media_stack.api.state import ControllerState as BootstrapState


class TestBootstrapState(unittest.TestCase):
    def test_initial_state_is_idle(self):
        state = BootstrapState()
        self.assertEqual(state.phase, "idle")
        self.assertFalse(state.is_running)
        self.assertFalse(state.is_complete)

    def test_start_sets_running(self):
        state = BootstrapState()
        state.start()
        self.assertEqual(state.phase, "running")
        self.assertTrue(state.is_running)
        self.assertFalse(state.is_complete)
        self.assertIsNotNone(state.started_at)

    def test_finish_success(self):
        state = BootstrapState()
        state.start()
        state.finish()
        self.assertEqual(state.phase, "complete")
        self.assertFalse(state.is_running)
        self.assertTrue(state.is_complete)
        self.assertIsNone(state.error)
        self.assertIsNotNone(state.completed_at)

    def test_finish_error(self):
        state = BootstrapState()
        state.start()
        state.finish(error="something broke")
        self.assertEqual(state.phase, "error")
        self.assertTrue(state.is_complete)
        self.assertEqual(state.error, "something broke")

    def test_complete_phase_tracking(self):
        state = BootstrapState()
        state.start()
        state.complete_phase("precheck")
        state.complete_phase("servarr")
        self.assertEqual(state.phases_completed, ["precheck", "servarr"])

    def test_record_preflight(self):
        state = BootstrapState()
        state.record_preflight("jellyfin", {"status": "ok", "key": "abc"})
        self.assertEqual(state.preflight_results["jellyfin"]["status"], "ok")

    def test_to_dict(self):
        # ADR-0005 Phase 5a: ``phase`` / ``phases_completed`` /
        # ``current_action`` were retired from the ``/status``
        # wire shape; the dataclass fields stay for internal
        # bookkeeping but no longer ship in ``to_dict()``.
        state = BootstrapState()
        state.start()
        state.complete_phase("test")
        state.record_preflight("jf", {"status": "ok"})
        state.finish()
        d = state.to_dict()
        self.assertNotIn("phase", d)
        self.assertNotIn("phases_completed", d)
        self.assertNotIn("current_action", d)
        self.assertIsNotNone(d["elapsed_seconds"])
        self.assertIn("jf", d["preflight_results"])
        # Internal bookkeeping intact.
        self.assertEqual(state.phase, "complete")
        self.assertIn("test", state.phases_completed)

    def test_to_dict_idle(self):
        state = BootstrapState()
        d = state.to_dict()
        self.assertNotIn("phase", d)
        self.assertIsNone(d["elapsed_seconds"])


if __name__ == "__main__":
    unittest.main()
