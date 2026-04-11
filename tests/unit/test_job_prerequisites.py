"""Tests for job prerequisite DAG dispatcher.

Covers:
- Prerequisite condition checking (reachable, api_key, media_server_id)
- Job skipping when prereqs aren't met
- Job execution when prereqs are met
- Sub-jobs don't run when parent prereqs fail
- Retry logic in run_all_media_server_jobs
- Prerequisite registry integrity
- Individual job prerequisite declarations
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.job_framework import (
    Job, JobContext, JobRunner, PREREQS, register_prereq,
    build_job_framework, get_job_registry,
    run_job, run_all_media_server_jobs,
    _prereq_media_server_id, _prereq_media_server_api_key,
    _prereq_media_server_reachable,
)


class TestPrereqConditions(unittest.TestCase):
    """Test individual prerequisite condition functions."""

    def test_media_server_id_true_when_set(self):
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        self.assertTrue(_prereq_media_server_id(ctx))

    def test_media_server_id_false_when_empty(self):
        ctx = JobContext()
        ctx._cfg_cache = {}
        ctx._profile_cache = {}
        self.assertFalse(_prereq_media_server_id(ctx))

    def test_media_server_api_key_true_when_in_env(self):
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-key"}):
            self.assertTrue(_prereq_media_server_api_key(ctx))

    def test_media_server_api_key_false_when_empty(self):
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": ""}, clear=False):
            # Also mock the discovery to return nothing
            with patch("media_stack.api.services.registry.read_api_key_from_file", return_value=""):
                with patch("media_stack.api.services.registry.read_api_key_via_http", return_value=""):
                    self.assertFalse(_prereq_media_server_api_key(ctx))

    def test_media_server_reachable_true(self):
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            self.assertTrue(_prereq_media_server_reachable(ctx))

    def test_media_server_reachable_false_on_timeout(self):
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        with patch("urllib.request.urlopen", side_effect=TimeoutError):
            self.assertFalse(_prereq_media_server_reachable(ctx))

    def test_media_server_reachable_false_when_no_url(self):
        ctx = JobContext()
        ctx._cfg_cache = {}
        ctx._profile_cache = {}
        self.assertFalse(_prereq_media_server_reachable(ctx))


class TestPrereqRegistry(unittest.TestCase):
    """Test the PREREQS registry integrity."""

    def test_all_prereqs_are_callable(self):
        for name, fn in PREREQS.items():
            self.assertTrue(callable(fn), f"PREREQS['{name}'] is not callable")

    def test_expected_prereqs_exist(self):
        expected = {"media_server_id", "media_server_api_key", "media_server_reachable"}
        self.assertEqual(set(PREREQS.keys()), expected)

    def test_every_job_prereq_exists_in_registry(self):
        """Every requires entry in the job tree must have a matching PREREQ."""
        root = build_job_framework()
        def _check(job):
            for req in job.requires:
                self.assertIn(req, PREREQS,
                              f"Job '{job.name}' requires '{req}' but it's not in PREREQS registry")
            for sub in job.sub_jobs:
                _check(sub)
        _check(root)


class TestJobPrereqGating(unittest.TestCase):
    """Test that jobs are gated by prerequisites."""

    def test_job_runs_when_prereqs_met(self):
        called = []
        def handler(ctx):
            called.append(True)

        job = Job("test", handler, requires=["media_server_id"])
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        result = job.run(ctx)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(called)

    def test_job_blocked_when_prereqs_not_met(self):
        called = []
        def handler(ctx):
            called.append(True)

        job = Job("test", handler, requires=["media_server_api_key"])
        ctx = JobContext()
        ctx._cfg_cache = {}
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": ""}, clear=False):
            with patch("media_stack.api.services.registry.read_api_key_from_file", return_value=""):
                with patch("media_stack.api.services.registry.read_api_key_via_http", return_value=""):
                    result = job.run(ctx)
        self.assertEqual(result["status"], "prereq_not_met")
        self.assertEqual(called, [], "Handler should NOT have been called")

    def test_sub_jobs_dont_run_when_parent_prereq_fails(self):
        sub_called = []
        parent = Job("parent", lambda ctx: {}, requires=["media_server_api_key"])
        parent.add_sub_job(Job("child", lambda ctx: sub_called.append(True)))

        ctx = JobContext()
        ctx._cfg_cache = {}
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": ""}, clear=False):
            with patch("media_stack.api.services.registry.read_api_key_from_file", return_value=""):
                with patch("media_stack.api.services.registry.read_api_key_via_http", return_value=""):
                    result = parent.run(ctx)
        self.assertEqual(result["status"], "prereq_not_met")
        self.assertEqual(sub_called, [], "Sub-job should NOT run when parent prereq fails")

    def test_job_with_no_prereqs_always_runs(self):
        called = []
        job = Job("test", lambda ctx: called.append(True))
        result = job.run(JobContext())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(called)

    def test_prereq_not_met_logs_wait(self):
        import media_stack.services.runtime_platform as rp
        logged = []
        original = rp.log
        rp.log = lambda msg: logged.append(msg)
        try:
            job = Job("test-job", lambda ctx: {}, requires=["media_server_api_key"])
            ctx = JobContext()
            ctx._cfg_cache = {}
            ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
            with patch.dict(os.environ, {"JELLYFIN_API_KEY": ""}, clear=False):
                with patch("media_stack.api.services.registry.read_api_key_from_file", return_value=""):
                    with patch("media_stack.api.services.registry.read_api_key_via_http", return_value=""):
                        job.run(ctx)
        finally:
            rp.log = original
        wait_logs = [l for l in logged if "[WAIT]" in l and "test-job" in l]
        self.assertGreater(len(wait_logs), 0, "Should log [WAIT] when prereq not met")


class TestJobTreePrereqs(unittest.TestCase):
    """Test the full bootstrap job tree has correct prerequisites."""

    def test_media_server_jobs_require_api_key(self):
        root = build_job_framework()
        ms_job = next(j for j in root.sub_jobs if j.name == "configure-media-server")
        self.assertIn("media_server_api_key", ms_job.requires)
        self.assertIn("media_server_reachable", ms_job.requires)

    def test_prewarm_requires_api_key(self):
        root = build_job_framework()
        # Prewarm is under configure-post phase
        from media_stack.cli.commands.job_framework import _find_job_in_tree
        prewarm = _find_job_in_tree(root, "refresh-media")
        self.assertIsNotNone(prewarm, "refresh-media not found in tree")
        self.assertIn("media_server_api_key", prewarm.requires)

    def test_download_clients_have_no_ms_prereqs(self):
        root = build_job_framework()
        dl = next(j for j in root.sub_jobs if j.name == "configure-download-clients")
        self.assertNotIn("media_server_api_key", dl.requires)
        self.assertNotIn("media_server_reachable", dl.requires)

    def test_root_job_has_no_prereqs(self):
        root = build_job_framework()
        self.assertEqual(root.requires, [])


class TestRunJobWithPrereqs(unittest.TestCase):
    """Test run_job applies correct prereqs per job."""

    def test_media_server_job_gets_prereqs(self):
        # Patch the PREREQS dict entries to return True
        with patch.dict(PREREQS, {
            "media_server_reachable": lambda ctx: True,
            "media_server_api_key": lambda ctx: True,
        }):
            with patch.dict(os.environ, {"JELLYFIN_API_KEY": "key"}):
                with patch("importlib.import_module") as mock_mod:
                    mock_mod.return_value = MagicMock()
                    result = run_job("configure-libraries")
        self.assertNotEqual(result.get("status"), "prereq_not_met")

    def test_categories_job_has_no_ms_prereqs(self):
        """configure-categories should run without media server prereqs."""
        with patch("importlib.import_module") as mock_mod:
            mock_mod.return_value = MagicMock()
            result = run_job("configure-categories")
        self.assertNotEqual(result.get("status"), "prereq_not_met")


class TestRunAllWithRetry(unittest.TestCase):
    """Test run_all_media_server_jobs waits for prerequisites."""

    def test_runs_immediately_when_prereqs_met(self):
        with patch.dict(PREREQS, {
            "media_server_reachable": lambda ctx: True,
            "media_server_api_key": lambda ctx: True,
        }):
            with patch.dict(os.environ, {"JELLYFIN_API_KEY": "key"}):
                with patch("importlib.import_module") as mock_mod:
                    mock_mod.return_value = MagicMock()
                    result = run_all_media_server_jobs(max_wait=1)
        self.assertEqual(result["status"], "ok")

    def test_ms_jobs_skipped_when_prereqs_not_met(self):
        """When prereqs fail, media server sub-jobs get prereq_not_met."""
        with patch.dict(PREREQS, {
            "media_server_reachable": lambda ctx: False,
            "media_server_api_key": lambda ctx: False,
        }):
            with patch(
                "media_stack.cli.commands.job_framework.JobRunner._try_satisfy_prereqs"
            ):
                result = run_all_media_server_jobs(max_wait=1)
        # Jobs with unmet prereqs should be skipped
        jobs = result.get("jobs", {})
        skipped = [n for n, r in jobs.items() if r.get("status") == "prereq_not_met"]
        self.assertGreater(len(skipped), 0, "No jobs were gated by prereqs")


class TestCheckPrereqs(unittest.TestCase):
    """Test Job.check_prereqs method directly."""

    def test_returns_none_when_all_pass(self):
        job = Job("test", lambda ctx: {}, requires=["media_server_id"])
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        self.assertIsNone(job.check_prereqs(ctx))

    def test_returns_reason_when_fails(self):
        job = Job("test", lambda ctx: {}, requires=["media_server_reachable"])
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        with patch("urllib.request.urlopen", side_effect=ConnectionRefusedError):
            reason = job.check_prereqs(ctx)
        self.assertIsNotNone(reason)
        self.assertIn("media_server_reachable", reason)

    def test_returns_none_for_no_prereqs(self):
        job = Job("test", lambda ctx: {})
        self.assertIsNone(job.check_prereqs(JobContext()))

    def test_unknown_prereq_passes(self):
        """Unknown prereq names should not crash — just pass (fail-open)."""
        job = Job("test", lambda ctx: {}, requires=["nonexistent_prereq"])
        self.assertIsNone(job.check_prereqs(JobContext()))


class TestJobFrameworkPluggable(unittest.TestCase):
    """Test that the Job framework is generic and pluggable."""

    def test_register_custom_prereq(self):
        """Any code can register new prereqs — not hardcoded to media server."""
        register_prereq("test_custom_check", lambda ctx: True)
        self.assertIn("test_custom_check", PREREQS)
        # Job can use it
        job = Job("test", lambda ctx: {}, requires=["test_custom_check"])
        self.assertIsNone(job.check_prereqs(JobContext()))
        # Cleanup
        del PREREQS["test_custom_check"]

    def test_custom_workflow_not_bootstrap(self):
        """Can create a completely different job tree — framework doesn't care."""
        order = []
        root = Job("my-workflow", lambda ctx: order.append("root"))
        root.add_sub_job(Job("step-1", lambda ctx: order.append("s1")))
        root.add_sub_job(Job("step-2", lambda ctx: order.append("s2")))
        result = root.run(JobContext())
        self.assertEqual(result["status"], "ok")
        self.assertEqual(order, ["root", "s1", "s2"])

    def test_sub_jobs_have_own_prereqs(self):
        """Sub-jobs check their own prereqs independently of parent."""
        register_prereq("always_false", lambda ctx: False)
        order = []
        root = Job("parent", lambda ctx: order.append("parent"))
        root.add_sub_job(Job("ok-child", lambda ctx: order.append("ok")))
        root.add_sub_job(Job("gated-child", lambda ctx: order.append("gated"),
                             requires=["always_false"]))
        root.add_sub_job(Job("another-ok", lambda ctx: order.append("another")))
        root.run(JobContext())
        # Parent and non-gated children run; gated child skipped
        self.assertIn("parent", order)
        self.assertIn("ok", order)
        self.assertNotIn("gated", order)
        self.assertIn("another", order)
        del PREREQS["always_false"]

    def test_n_level_nesting_with_prereqs(self):
        """N-level deep jobs each check their own prereqs."""
        register_prereq("level3_gate", lambda ctx: False)
        order = []
        l1 = Job("L1", lambda ctx: order.append("L1"))
        l2 = Job("L2", lambda ctx: order.append("L2"))
        l3 = Job("L3", lambda ctx: order.append("L3"), requires=["level3_gate"])
        l4 = Job("L4", lambda ctx: order.append("L4"))
        l2.add_sub_job(l3)
        l3.add_sub_job(l4)  # L4 won't run because L3 is gated
        l1.add_sub_job(l2)
        l1.run(JobContext())
        self.assertEqual(order, ["L1", "L2"])  # L3 and L4 skipped
        del PREREQS["level3_gate"]

    def test_job_runner_retries_then_runs(self):
        """JobRunner retries prereqs then executes when satisfied."""
        call_count = {"n": 0}
        def prereq_passes_after_retries(ctx):
            call_count["n"] += 1
            return call_count["n"] >= 3  # checked twice per round (ready + deferred)

        register_prereq("slow_prereq", prereq_passes_after_retries)
        called = []
        job = Job("test", lambda ctx: called.append(True), requires=["slow_prereq"])
        with patch.object(JobRunner, "_try_satisfy_prereqs"):
            result = JobRunner(job, JobContext(), max_attempts=3).run()
        self.assertTrue(called)
        self.assertEqual(result["status"], "ok")
        del PREREQS["slow_prereq"]

    def test_find_job_in_tree(self):
        """run_job finds jobs deep in the tree with their prereqs."""
        from media_stack.cli.commands.job_framework import _find_job_in_tree
        root = build_job_framework()
        lib_job = _find_job_in_tree(root, "configure-libraries")
        self.assertIsNotNone(lib_job)
        self.assertEqual(lib_job.name, "configure-libraries")
        # Parent's prereqs don't leak to child
        # (configure-libraries itself has no requires — parent does)

    def test_add_sub_job_returns_self(self):
        """add_sub_job returns self for fluent chaining."""
        root = Job("root", lambda ctx: {})
        returned = root.add_sub_job(Job("child", lambda ctx: {}))
        self.assertIs(returned, root)


if __name__ == "__main__":
    unittest.main()
