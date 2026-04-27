"""Tests for the Phase-3 ``stdout_tail`` passthrough in ``JobRunner``.

When a job's result dict carries a ``stdout_tail`` string, the runner
forwards it verbatim to ``record_run_complete`` so the LastRunPanel
shows the tail under "Output tail (last N chars)".

We don't capture stdout at the framework level (mixing concurrent
async jobs through a single sys.stdout would scramble output across
threads). The contract is purely opt-in: jobs that subprocess and
want to surface stdout copy the last N bytes into their result.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.application.jobs.framework import (  # noqa: E402
    Job, JobContext, JobRunner,
)
from media_stack.application.jobs.run_history import (  # noqa: E402
    iter_records,
)


class TestStdoutTailPassthrough(unittest.TestCase):
    def setUp(self) -> None:
        # Run-history file lives under CONFIG_ROOT; isolate per test.
        self._tmpdir = tempfile.mkdtemp(prefix="runner-stdout-tail-")
        self._env_patcher = patch.dict(
            os.environ, {"CONFIG_ROOT": self._tmpdir},
        )
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_sync_job_stdout_tail_surfaces_in_run_record(self) -> None:
        def handler(_ctx):
            return {
                "status": "ok",
                "stdout_tail": "line A\nline B\nline C\n",
            }

        job = Job("emit-stdout", handler)
        runner = JobRunner(job, JobContext(), source="manual")
        runner.run()

        # JobRunner emits two records per single-job batch: a parent
        # "batch" record (parent_run_id=None) and a child per-job record
        # (parent_run_id=<batch>). Both share the root's name. The
        # stdout_tail belongs on the CHILD — assert against that only.
        records = list(iter_records())
        child_records = [
            r for r in records
            if r.job_name == "emit-stdout" and r.parent_run_id is not None
        ]
        self.assertEqual(len(child_records), 1)
        self.assertEqual(
            child_records[0].stdout_tail, "line A\nline B\nline C\n",
        )

    def test_sync_job_without_stdout_tail_records_none(self) -> None:
        def handler(_ctx):
            return {"status": "ok"}

        job = Job("no-stdout", handler)
        runner = JobRunner(job, JobContext(), source="manual")
        runner.run()

        records = list(iter_records())
        child_records = [
            r for r in records
            if r.job_name == "no-stdout" and r.parent_run_id is not None
        ]
        self.assertEqual(len(child_records), 1)
        self.assertIsNone(child_records[0].stdout_tail)

    def test_non_string_stdout_tail_is_ignored(self) -> None:
        # A bug in a job emitting a list / dict for stdout_tail must not
        # propagate as garbage into the record — the runner only forwards
        # str values.
        def handler(_ctx):
            return {
                "status": "ok",
                "stdout_tail": ["line A", "line B"],  # wrong type
            }

        job = Job("typed-wrong", handler)
        runner = JobRunner(job, JobContext(), source="manual")
        runner.run()

        records = list(iter_records())
        child_records = [
            r for r in records
            if r.job_name == "typed-wrong" and r.parent_run_id is not None
        ]
        self.assertEqual(len(child_records), 1)
        self.assertIsNone(child_records[0].stdout_tail)


if __name__ == "__main__":
    unittest.main()
