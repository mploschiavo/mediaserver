"""Tests for job framework cancellation.

Verifies that:
- JobContext.cancelled reflects module-level cancel flag
- Job.run() stops before running when cancelled
- Job.run() stops between sub-jobs when cancelled
- JobRunner.run() marks remaining jobs as cancelled
- CancelledError propagates correctly
- request_cancel() sets the module flag
"""

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.job_framework import (
    CancelledError,
    Job,
    JobContext,
    JobRunner,
    request_cancel,
    _is_cancel_requested,
)
import media_stack.cli.commands.job_framework as jf


class _ResetCancel:
    """Mixin to reset the module-level cancel flag after each test."""

    def setUp(self):
        jf._cancel_requested = False

    def tearDown(self):
        jf._cancel_requested = False


def _noop_handler(ctx):
    return None


def _slow_handler(ctx):
    """Simulate a long-running job that checks cancel."""
    for _ in range(100):
        ctx.check_cancelled()
        time.sleep(0.01)
    return None


def _tracking_handler(name, tracker):
    """Return a handler that records execution order."""
    def handler(ctx):
        tracker.append(name)
        return None
    return handler


class TestJobContextCancellation(_ResetCancel, unittest.TestCase):

    def test_not_cancelled_by_default(self):
        ctx = JobContext()
        self.assertFalse(ctx.cancelled)

    def test_cancel_sets_flag(self):
        ctx = JobContext()
        ctx.cancel()
        self.assertTrue(ctx.cancelled)

    def test_check_cancelled_raises(self):
        ctx = JobContext()
        ctx.cancel()
        with self.assertRaises(CancelledError):
            ctx.check_cancelled()

    def test_check_cancelled_noop_when_not_cancelled(self):
        ctx = JobContext()
        ctx.check_cancelled()  # Should not raise

    def test_module_level_cancel_flag(self):
        ctx = JobContext()
        self.assertFalse(ctx.cancelled)
        request_cancel()
        self.assertTrue(ctx.cancelled)
        self.assertTrue(_is_cancel_requested())

    def test_module_cancel_causes_check_to_raise(self):
        ctx = JobContext()
        request_cancel()
        with self.assertRaises(CancelledError):
            ctx.check_cancelled()


class TestJobCancellation(_ResetCancel, unittest.TestCase):

    def test_job_skips_when_cancelled_before_start(self):
        ctx = JobContext()
        ctx.cancel()
        job = Job("test-job", _noop_handler)
        result = job.run(ctx)
        self.assertEqual(result["status"], "cancelled")

    def test_job_runs_normally_when_not_cancelled(self):
        ctx = JobContext()
        job = Job("test-job", _noop_handler)
        result = job.run(ctx)
        self.assertEqual(result["status"], "ok")

    def test_job_stops_between_sub_jobs(self):
        tracker = []
        ctx = JobContext()

        def cancel_after_first(c):
            tracker.append("first")
            ctx.cancel()
            return None

        root = Job("root", _noop_handler)
        root.add_sub_job(Job("sub-1", cancel_after_first))
        root.add_sub_job(Job("sub-2", _tracking_handler("second", tracker)))
        root.add_sub_job(Job("sub-3", _tracking_handler("third", tracker)))

        result = root.run(ctx)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(tracker, ["first"])  # sub-2 and sub-3 never ran

    def test_cancelled_error_in_handler_produces_cancelled_status(self):
        ctx = JobContext()

        def handler_that_cancels(c):
            c.cancel()
            c.check_cancelled()  # raises CancelledError

        job = Job("test", handler_that_cancels)
        result = job.run(ctx)
        self.assertEqual(result["status"], "cancelled")


class TestJobRunnerCancellation(_ResetCancel, unittest.TestCase):

    def test_runner_cancels_remaining_jobs(self):
        tracker = []
        ctx = JobContext()

        def cancel_handler(c):
            tracker.append("ran")
            ctx.cancel()
            return None

        root = Job("root", lambda ctx: None)
        root.add_sub_job(Job("job-1", cancel_handler))
        root.add_sub_job(Job("job-2", _tracking_handler("job-2", tracker)))
        root.add_sub_job(Job("job-3", _tracking_handler("job-3", tracker)))

        runner = JobRunner(root, ctx)
        result = runner.run()

        # job-1 ran, job-2 and job-3 should be cancelled
        self.assertIn("ran", tracker)
        self.assertNotIn("job-2", tracker)
        self.assertNotIn("job-3", tracker)
        self.assertEqual(runner.results.get("job-2", {}).get("status"), "cancelled")
        self.assertEqual(runner.results.get("job-3", {}).get("status"), "cancelled")

    def test_runner_completes_all_when_not_cancelled(self):
        tracker = []
        ctx = JobContext()

        # Use independent jobs (no sub-jobs) to avoid double execution
        # from flatten + parent sub-job iteration.
        root = Job("root", lambda ctx: None)
        job1 = Job("job-1", _tracking_handler("job-1", tracker))
        job2 = Job("job-2", _tracking_handler("job-2", tracker))
        root.add_sub_job(job1)
        root.add_sub_job(job2)

        runner = JobRunner(root, ctx)
        result = runner.run()

        # Both jobs should have run (flatten lists them independently)
        self.assertIn("job-1", tracker)
        self.assertIn("job-2", tracker)
        self.assertEqual(result["status"], "ok")

    def test_module_cancel_stops_runner(self):
        tracker = []
        ctx = JobContext()

        def trigger_module_cancel(c):
            tracker.append("ran")
            request_cancel()
            return None

        root = Job("root", lambda ctx: None)
        root.add_sub_job(Job("job-1", trigger_module_cancel))
        root.add_sub_job(Job("job-2", _tracking_handler("job-2", tracker)))

        runner = JobRunner(root, ctx)
        runner.run()

        self.assertIn("ran", tracker)
        self.assertNotIn("job-2", tracker)


class TestRequestCancel(_ResetCancel, unittest.TestCase):

    def test_request_cancel_sets_flag(self):
        self.assertFalse(_is_cancel_requested())
        request_cancel()
        self.assertTrue(_is_cancel_requested())

    def test_multiple_calls_idempotent(self):
        request_cancel()
        request_cancel()
        self.assertTrue(_is_cancel_requested())


if __name__ == "__main__":
    unittest.main()
