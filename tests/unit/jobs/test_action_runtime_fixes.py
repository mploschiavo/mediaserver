"""Ratchets for v1.0.105: bootstrap visibility + correctness.

ADR-0005 Phase 5c.4 (in-process action loop)
--------------------------------------------

The original ratchets pinned three properties of the multiprocessing-
spawn worker that owned action dispatch. That worker was retired in
ADR-0005 Phase 5c.4 in favour of an in-process daemon thread that
calls ``_dispatch_action`` -> ``run_job`` -> ``JobRunner.run``
directly. The same three end-user invariants still apply, but the
mechanism for each is different:

  1. **No fork-deadlock.** The original incident (2026-04-22)
     was ``fork()`` inheriting the controller's logging-module
     lock as permanently held in the child. The in-process loop
     doesn't fork at all, so the deadlock surface is gone by
     construction. The ratchet now asserts the legacy spawn-
     context machinery is *absent* (any reintroduction would
     recreate the original failure mode).

  2. **Timeout enforcement actually stops the action.** The
     legacy parent ``terminate()``d the subprocess. The new
     watchdog daemon thread calls
     ``framework.request_cancel()`` which raises
     ``CancelledError`` at the JobRunner's next prereq /
     check_cancelled boundary. Same end-user contract, no
     SIGKILL.

  3. **Per-action heartbeat every 60s.** The watchdog thread
     emits ``[ACTION] X: still running (Ts elapsed, timeout Ys)``
     every 60s, matching the legacy subprocess heartbeat. The
     per-job ``[JOB]`` line in the JobRunner is unchanged.

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


class NoForkBasedActionSubprocesses(unittest.TestCase):
    """ADR-0005 Phase 5c.4: actions run on a daemon thread, not a
    subprocess. The original fork-deadlock surface (2026-04-22
    incident) is gone by construction — re-introducing
    ``multiprocessing.Process`` of any kind in the action path
    would resurrect it.
    """

    def test_no_multiprocessing_in_controller_serve(self) -> None:
        """Strip comments + the module docstring, then assert no
        live ``multiprocessing`` use survives. Block comments and
        the file-level docstring are allowed to *reference* the
        legacy symbols — the file documents the migration.
        """
        import ast
        path = ROOT / "src/media_stack/cli/commands/controller_serve.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        # Drop the module-level docstring (first Expr/Constant node)
        # and any function-level docstrings so block-quote references
        # to the legacy symbols don't trip the ratchet.
        ast.get_docstring(tree)  # warm the ast.AST.body[0] check
        if (
            tree.body
            and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
        ):
            tree.body.pop(0)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                # Replace docstrings with empty string so the
                # ``ast.unparse`` round-trip below can't catch them.
                node.value.value = ""
        live_source = ast.unparse(tree)
        self.assertNotIn(
            "import multiprocessing", live_source,
            "controller_serve.py re-imports multiprocessing — the "
            "in-process action loop must not spawn subprocesses "
            "(the 18-min fork-deadlock incident).",
        )
        self.assertNotIn(
            "multiprocessing.Process(", live_source,
            "controller_serve.py spawns a multiprocessing.Process — "
            "the in-process action loop must not spawn subprocesses.",
        )
        self.assertNotIn(
            'get_context("spawn")', live_source,
            "controller_serve.py re-introduced spawn context — the "
            "in-process action loop has no subprocess to spawn.",
        )

    def test_action_runs_on_daemon_thread(self) -> None:
        path = ROOT / "src/media_stack/cli/commands/controller_serve.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(
            "action-dispatch", text,
            "Action-dispatch thread name removed — the in-process "
            "action loop's worker thread should be named so it shows "
            "up in py-spy / faulthandler dumps.",
        )


class TimeoutActuallyEnforced(unittest.TestCase):

    def test_watchdog_requests_cancel_at_timeout(self) -> None:
        # ADR-0015 Phase 7e moved the watchdog onto
        # :class:`ActionWatchdog` in workflows/. The anti-regression grep
        # follows the symbols to their new home.
        path = ROOT / "src/media_stack/cli/workflows/controller_action_dispatcher.py"
        text = path.read_text(encoding="utf-8")
        # The in-process equivalent of subprocess termination:
        # the watchdog thread sets ``timed_out`` and calls
        # ``framework.request_cancel()`` so the next prereq /
        # check_cancelled boundary in the JobRunner raises
        # ``CancelledError``.
        self.assertIn(
            "timed_out", text,
            "Timeout-enforcement variable removed — bootstrap can "
            "spin past the configured limit (the 18-min incident).",
        )
        self.assertRegex(
            text,
            r"if elapsed >= self\._timeout_seconds",
            "Timeout-enforcement check missing — bootstrap can "
            "spin past the configured limit.",
        )
        self.assertIn(
            "request_cancel", text,
            "Watchdog no longer calls ``framework.request_cancel`` on "
            "timeout; the JobRunner has nothing to react to.",
        )


class HeartbeatAndPerJobLogs(unittest.TestCase):

    def test_action_heartbeat_every_60s(self) -> None:
        # ADR-0015 Phase 7e: heartbeat logic moved onto
        # :class:`ActionWatchdog`. Anti-regression grep follows.
        path = ROOT / "src/media_stack/cli/workflows/controller_action_dispatcher.py"
        text = path.read_text(encoding="utf-8")
        # The in-process watchdog emits "still running" every 60s;
        # the variable name changed from ``t_last_heartbeat`` (legacy
        # subprocess shape) to ``next_heartbeat`` (the watchdog's
        # absolute-time bookkeeping).
        self.assertIn(
            "still running", text,
            "Per-action heartbeat log line removed — long actions "
            "(~14 min discover-indexers) make the dashboard look "
            "frozen.",
        )
        self.assertIn(
            "next_heartbeat", text,
            "Heartbeat scheduling variable removed — verify the "
            "watchdog still emits one heartbeat per 60s.",
        )

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
