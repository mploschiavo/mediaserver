"""Regression: action_bootstrap must invoke the jobs framework.

Catches the bug where fresh compose installs had Jellyfin libraries
and Live TV not configured because adapter_hooks were missing from
the generated config JSON. The jobs framework is a parallel path
driven by contracts/services/*.yaml that does not depend on
adapter_hooks.
"""

from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.jobs.action_handlers import (  # noqa: E402
    ActionHandlerService,
    _instance as _svc,
)


class ActionBootstrapRunsJobsFrameworkTests(unittest.TestCase):
    def _call(self, env: dict[str, str] | None = None) -> MagicMock:
        args = argparse.Namespace(config="", config_root="")
        state = object()
        run_preflights = MagicMock()
        persist_keys = MagicMock()
        build_runner = MagicMock(return_value=(MagicMock(), MagicMock()))
        with patch.dict(os.environ, env or {}, clear=False), patch.object(
            _svc, "_run_jobs_framework"
        ) as mock_jobs:
            _svc.action_bootstrap(args, state, run_preflights, persist_keys, build_runner)
        return mock_jobs

    def test_jobs_framework_invoked_by_default(self):
        env = {k: v for k, v in os.environ.items() if k != "BOOTSTRAP_SKIP_JOBS_FRAMEWORK"}
        with patch.dict(os.environ, env, clear=True):
            mock = self._call()
        mock.assert_called_once()

    def test_jobs_framework_skipped_when_env_set(self):
        mock = self._call({"BOOTSTRAP_SKIP_JOBS_FRAMEWORK": "1"})
        mock.assert_not_called()

    def test_run_jobs_framework_builds_and_runs(self):
        """The private helper should build the tree and dispatch through JobRunner."""
        svc = ActionHandlerService()
        fake_root = MagicMock()
        fake_result = {"results": {"j1": {"status": "ok"}, "j2": {"status": "skipped"}}}

        with patch(
            "media_stack.services.jobs.framework.build_job_framework",
            return_value=fake_root,
        ), patch(
            "media_stack.services.jobs.framework.JobRunner"
        ) as mock_runner:
            mock_runner.return_value.run.return_value = fake_result
            svc._run_jobs_framework()
        mock_runner.assert_called_once()
        mock_runner.return_value.run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
