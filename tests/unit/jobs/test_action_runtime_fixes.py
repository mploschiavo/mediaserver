"""Ratchets for v1.0.105: bootstrap visibility + correctness.

Three fixes pinned here, each with a "why" the next refactor
needs to keep working:

  1. **Spawn instead of fork for action subprocesses.**
     The 2026-04-22 incident: bootstrap auto-queued at startup
     logged ``[ACTION] bootstrap: starting`` and then EMITTED
     ZERO LOGS for 18+ minutes (the entire 600s timeout window
     and beyond). Cause: the controller has 6 background threads
     (HTTP server, audit verifier, snapshot timer, scheduled
     reconciler, user-reconcile, audit-verify); Python's
     multiprocessing default on Linux is fork(); fork inherits
     all those threads' locks as PERMANENTLY HELD in the child;
     the child's first lock acquire (logging.Logger uses an
     internal lock on every log call) deadlocks. Spawn creates a
     fresh interpreter, so no inherited locks.

  2. **Timeout enforcement actually kills the subprocess.**
     The previous ``timeout=600s`` was informational only — the
     parent loop sat in ``while worker.is_alive()`` indefinitely.
     Now the parent monitors elapsed time and ``terminate()``s the
     subprocess at the limit.

  3. **Per-job heartbeat + per-job complete log.** The job tree
     used to log ``[INFO] JobRunner: N jobs to dispatch`` once,
     then go silent until ``[INFO] JobRunner: complete``. Long
     jobs (e.g. discover-indexers, ~14 min) made the dashboard
     look frozen. Now each job emits ``[JOB] X: <status> (<elapsed>s)
     — N/M done, R remaining`` on completion, AND the parent
     emits a heartbeat every 60s of subprocess silence.

  4. **Skip-forced-password-rotation env var** for testing —
     ``STACK_ADMIN_SKIP_FORCED_ROTATION=1`` suppresses the
     "rotate on first login" gate so the
     ``compose down -v && up`` testing cycle doesn't require a
     password reset every iteration.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))


class SpawnMethodForActionSubprocesses(unittest.TestCase):

    def test_uses_spawn_context_not_fork(self) -> None:
        path = ROOT / "src/media_stack/cli/commands/controller_serve.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            'multiprocessing.get_context("spawn")', text,
            "controller_serve.py reverted to fork() for action "
            "subprocesses — first lock acquire in the child will "
            "deadlock (the 18-min bootstrap-silence incident).",
        )
        # Process spawn must use the spawn context, not the bare
        # multiprocessing.Process (which respects the global
        # default which IS fork on Linux).
        self.assertNotIn(
            "multiprocessing.Process(", text,
            "controller_serve.py uses bare multiprocessing.Process; "
            "should be _MP_CTX.Process so the spawn context applies.",
        )


class TimeoutActuallyEnforced(unittest.TestCase):

    def test_parent_kills_subprocess_at_timeout(self) -> None:
        path = ROOT / "src/media_stack/cli/commands/controller_serve.py"
        text = path.read_text(encoding="utf-8")
        # Look for the timeout-enforcement block: monotonic timer +
        # worker.terminate() under an `elapsed > timeout_seconds`
        # check.
        self.assertIn("timed_out", text,
                      "Timeout-enforcement variable removed.")
        self.assertRegex(
            text,
            r"if not timed_out and elapsed > timeout_seconds",
            "Timeout enforcement check missing — bootstrap can "
            "spin past the configured limit (the 18-min incident).",
        )


class HeartbeatAndPerJobLogs(unittest.TestCase):

    def test_subprocess_heartbeat_every_60s(self) -> None:
        path = ROOT / "src/media_stack/cli/commands/controller_serve.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("still running", text)
        self.assertIn("t_last_heartbeat", text)

    def test_jobrunner_logs_per_job_completion_with_progress(self) -> None:
        # services/jobs/framework.py is now a sys.modules-alias shim;
        # the impl lives in application/jobs/framework.py (Phase 16-E).
        path = ROOT / "src/media_stack/application/jobs/framework.py"
        text = path.read_text(encoding="utf-8")
        # The new completion log: "[JOB] X: <status> (<elapsed>s) — N/M done"
        self.assertRegex(
            text,
            r'\[JOB\] \{job\.name\}: \{_status\}',
            "Per-job completion log dropped — the dashboard goes "
            "silent between job starts again.",
        )
        self.assertIn("done, ", text)


class SkipForcedRotationEnvVar(unittest.TestCase):

    def test_env_var_short_circuits_needs_rotation(self) -> None:
        # The skip-rotation env-var check moved from the legacy
        # ``handlers_get.py`` lambda into the
        # ``ForcedRotationGate.needs_rotation_for`` method on
        # ``api/routes/users_get.py`` during ADR-0007 Phase 2 Phase E.
        # Assert the env var name + the bootstrap-sources guard tuple
        # both still live there so flipping the env knob only
        # suppresses rotation for env-seed/env-legacy callers.
        path = ROOT / "src/media_stack/api/routes/users_get.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("STACK_ADMIN_SKIP_FORCED_ROTATION", text)
        # The check must AND with the source check: only env-seed /
        # env-legacy sources can be suppressed.
        self.assertIn('"env-seed"', text)
        self.assertIn('"env-legacy"', text)
        # Source-tuple guard must remain a short-circuit ahead of the
        # truthy-env check so non-bootstrap sources are never
        # incorrectly flagged as not-needing-rotation.
        self.assertRegex(
            text,
            r'if source\.lower\(\) not in _BOOTSTRAP_SOURCES:\s*\n'
            r'\s*return False\s*\n'
            r'\s*return self\._skip not in _TRUTHY',
            "needs_rotation logic regressed — skip env var should "
            "AND with source check, not OR or replace.",
        )

    def test_compose_documents_the_env_var(self) -> None:
        text = (ROOT / "deploy" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("STACK_ADMIN_SKIP_FORCED_ROTATION", text)
        # Must include the safety warning so a copy-paster doesn't
        # ship it on an internet-exposed stack. The comment can
        # span multiple lines, so DOTALL match the key phrases.
        self.assertTrue(
            re.search(r"NEVER set.*internet", text, re.DOTALL),
            "Compose docs must warn against using "
            "STACK_ADMIN_SKIP_FORCED_ROTATION on internet-exposed "
            "stacks (the env-seed credential is well-known).",
        )


if __name__ == "__main__":
    unittest.main()
