"""Tests for bootstrap job decomposition framework."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.bootstrap_jobs import (  # noqa: E402
    Job, JobContext, build_bootstrap_jobs, get_job_registry, run_job,
)


class TestJobFramework(unittest.TestCase):
    def test_job_runs_handler(self):
        called = []
        def handler(ctx):
            called.append(True)
            return {"key": "val"}
        job = Job("test-job", handler)
        result = job.run(JobContext())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(called)

    def test_job_catches_errors(self):
        def handler(ctx):
            raise RuntimeError("boom")
        job = Job("failing-job", handler)
        result = job.run(JobContext())
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["error"])

    def test_job_runs_sub_jobs(self):
        order = []
        def parent(ctx):
            order.append("parent")
        def child1(ctx):
            order.append("child1")
        def child2(ctx):
            order.append("child2")
        job = Job("parent", parent)
        job.add_sub_job(Job("child1", child1))
        job.add_sub_job(Job("child2", child2))
        job.run(JobContext())
        self.assertEqual(order, ["parent", "child1", "child2"])

    def test_sub_job_failure_doesnt_stop_siblings(self):
        order = []
        def child1(ctx):
            order.append("child1")
            raise RuntimeError("fail")
        def child2(ctx):
            order.append("child2")
        job = Job("parent", lambda ctx: None)
        job.add_sub_job(Job("child1", child1))
        job.add_sub_job(Job("child2", child2))
        job.run(JobContext())
        self.assertIn("child2", order)

    def test_job_has_elapsed(self):
        job = Job("quick", lambda ctx: {})
        result = job.run(JobContext())
        self.assertIn("elapsed", result)
        self.assertGreaterEqual(result["elapsed"], 0)

    def test_n_level_deep_nesting(self):
        """Jobs can nest to arbitrary depth (N levels)."""
        order = []
        def make_handler(name):
            return lambda ctx: order.append(name)
        level1 = Job("level1", make_handler("L1"))
        level2 = Job("level2", make_handler("L2"))
        level3 = Job("level3", make_handler("L3"))
        level4 = Job("level4", make_handler("L4"))
        level3.add_sub_job(level4)
        level2.add_sub_job(level3)
        level1.add_sub_job(level2)
        level1.run(JobContext())
        self.assertEqual(order, ["L1", "L2", "L3", "L4"])


class TestJobContext(unittest.TestCase):
    def test_context_has_config_root(self):
        ctx = JobContext()
        self.assertIsInstance(ctx.config_root, str)

    @patch.dict(os.environ, {"STACK_ADMIN_USERNAME": "testuser"})
    def test_context_reads_env(self):
        ctx = JobContext()
        self.assertEqual(ctx.admin_username, "testuser")

    def test_media_server_id_from_profile(self):
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "emby"}}
        self.assertEqual(ctx.media_server_id(), "emby")


class TestJobRegistry(unittest.TestCase):
    def test_registry_has_expected_jobs(self):
        registry = get_job_registry()
        expected = {"configure-libraries", "configure-livetv", "configure-plugins",
                    "configure-playback", "configure-categories"}
        self.assertEqual(set(registry.keys()), expected)

    def test_all_handlers_callable(self):
        for name, handler in get_job_registry().items():
            self.assertTrue(callable(handler), f"{name} is not callable")


class TestBuildBootstrapJobs(unittest.TestCase):
    def test_root_job_is_bootstrap(self):
        root = build_bootstrap_jobs()
        self.assertEqual(root.name, "bootstrap")

    def test_has_media_server_sub_job(self):
        root = build_bootstrap_jobs()
        names = [j.name for j in root.sub_jobs]
        self.assertIn("configure-media-server", names)

    def test_media_server_has_library_sub_job(self):
        root = build_bootstrap_jobs()
        ms = next(j for j in root.sub_jobs if j.name == "configure-media-server")
        sub_names = [j.name for j in ms.sub_jobs]
        self.assertIn("configure-libraries", sub_names)
        self.assertIn("configure-livetv", sub_names)


class TestRunJob(unittest.TestCase):
    def test_unknown_job_returns_error(self):
        result = run_job("nonexistent-job-xyz")
        self.assertIn("error", result)
        self.assertIn("known", result)

    @patch("media_stack.cli.commands.bootstrap_jobs._configure_libraries")
    def test_run_job_calls_handler(self, mock_handler):
        mock_handler.return_value = {"service": "jellyfin"}
        result = run_job("configure-libraries")
        mock_handler.assert_called_once()


class TestKnownActionsIncludeJobs(unittest.TestCase):
    def test_all_jobs_in_known_actions(self):
        from media_stack.api.handlers_post import KNOWN_ACTIONS
        for job_name in get_job_registry():
            self.assertIn(job_name, KNOWN_ACTIONS, f"Job {job_name} not in KNOWN_ACTIONS")

    def test_configure_media_server_in_known_actions(self):
        from media_stack.api.handlers_post import KNOWN_ACTIONS
        self.assertIn("configure-media-server", KNOWN_ACTIONS)

    def test_all_jobs_have_priority(self):
        from media_stack.api.server import ACTION_PRIORITY
        for job_name in get_job_registry():
            self.assertIn(job_name, ACTION_PRIORITY, f"Job {job_name} has no priority")


if __name__ == "__main__":
    unittest.main()
