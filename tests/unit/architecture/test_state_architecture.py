"""Tests for ControllerState architecture — log buffer, runtime
config, failed services, deployment-state persistence, and
``ActionRecord`` value-object semantics.

ADR-0005 Phase 5c.4c retired the action-lifecycle surface that
this file used to cover (``start_action`` / ``finish_action`` /
``cancel_action`` / ``add_pending`` / ``pop_pending`` /
``get_action`` / ``action_running``); the architecture ratchet
``test_no_controller_state_action_lifecycle.py`` pins their
absence. ``ActionRecord`` itself remains as a small public value
object, kept here so external operator scripts that import it for
shape-typing keep working.
"""

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.api.state import ActionRecord, ActionStatus, ControllerState  # noqa: E402
from media_stack.services.runtime_platform import current_action_tag  # noqa: E402


class TestActionRecord(unittest.TestCase):
    """``ActionRecord`` is now a pure value object — production
    no longer constructs it (Phase 5c.4c). The tests stay so any
    external script that imports it for shape-typing has its
    contract pinned."""

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
        import time
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


class TestControllerStateLogs(unittest.TestCase):
    """``state.append_log`` reads its action-tag off
    ``runtime_platform.current_action_tag`` (Phase 5c.4c contextvar)
    instead of the retired ``current_action`` field."""

    def test_append_and_get(self):
        s = ControllerState()
        s.append_log("hello")
        logs = s.get_logs_since(0)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0][2], "hello")

    def test_filter_by_action(self):
        s = ControllerState()
        with current_action_tag("bootstrap"):
            s.append_log("line1")
        with current_action_tag("reconcile"):
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
    """``pending_actions`` is now always empty (the priority queue
    is the source of truth post-Phase-5c.4c). ``clear_pending``
    survives because operator clear-queue tooling still calls it;
    test the no-op shape it now has."""

    def test_clear_empty_pending(self):
        s = ControllerState()
        count = s.clear_pending()
        self.assertEqual(count, 0)
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
        # ADR-0005 Phase 5a/5c.4c: legacy bootstrap-progress fields
        # (``phase`` / ``phases_completed`` / ``current_action`` /
        # ``action_history``) are no longer serialized — Job
        # framework provides the canonical view via
        # ``/api/jobs/running`` + history.
        s = ControllerState()
        s.append_log("hello")
        d = s.to_dict()
        self.assertIn("initial_bootstrap_done", d)
        self.assertNotIn("phase", d)
        self.assertNotIn("phases_completed", d)
        self.assertNotIn("current_action", d)
        self.assertNotIn("action_history", d)


class TestInitialBootstrapDonePersistence(unittest.TestCase):
    """``initial_bootstrap_done`` rides the same ``runtime-config.json``
    sidecar as ``runtime_config`` so a controller restart on an
    already-bootstrapped install doesn't wedge the dashboard banner.

    Pins the regression: prior to this work, the flag was
    in-memory only; every redeploy reset it to ``False`` and the
    UI banner showed Queued indefinitely until a re-bootstrap
    completed."""

    def _state_with_isolated_persistence(self, tmp_path: Path) -> ControllerState:
        s = ControllerState()
        # Redirect the persistence file to a temp location for test
        # isolation — the production constant is an absolute path
        # under ``/srv-config/.controller``.
        s._RUNTIME_CONFIG_FILE = str(tmp_path / "runtime-config.json")
        return s

    def test_mark_initial_bootstrap_done_persists_flag(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            s = self._state_with_isolated_persistence(tmp)
            self.assertFalse(s.initial_bootstrap_done)
            s.mark_initial_bootstrap_done()
            self.assertTrue(s.initial_bootstrap_done)
            # The on-disk sidecar carries the flag.
            saved = json.loads(Path(s._RUNTIME_CONFIG_FILE).read_text())
            self.assertIs(saved.get("_initial_bootstrap_done"), True)

    def test_load_persisted_config_restores_flag_after_restart(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Simulate a prior controller life that successfully
            # bootstrapped.
            s_prev = self._state_with_isolated_persistence(tmp)
            s_prev.mark_initial_bootstrap_done()

            # New ControllerState (simulates restart). Flag starts at
            # False; load_persisted_config reads the sidecar.
            s_new = self._state_with_isolated_persistence(tmp)
            self.assertFalse(s_new.initial_bootstrap_done)
            s_new.load_persisted_config()
            self.assertTrue(s_new.initial_bootstrap_done)

    def test_finish_without_error_persists_flag(self):
        import tempfile, json
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            s = self._state_with_isolated_persistence(tmp)
            s.finish()  # success path
            self.assertTrue(s.initial_bootstrap_done)
            saved = json.loads(Path(s._RUNTIME_CONFIG_FILE).read_text())
            self.assertIs(saved.get("_initial_bootstrap_done"), True)

    def test_finish_with_error_does_not_persist_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            s = self._state_with_isolated_persistence(tmp)
            s.finish(error="kaboom")  # error path
            self.assertFalse(s.initial_bootstrap_done)
            # Sidecar wasn't written (or, if it was for some other
            # reason, doesn't carry the flag).
            sidecar = Path(s._RUNTIME_CONFIG_FILE)
            if sidecar.is_file():
                import json
                saved = json.loads(sidecar.read_text())
                self.assertNotIn("_initial_bootstrap_done", saved)


if __name__ == "__main__":
    unittest.main()
