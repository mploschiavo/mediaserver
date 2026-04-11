"""Integration tests: verify media server jobs ACTUALLY configure Jellyfin.

These tests exercise the real job chain with real config loading.
Only external I/O (HTTP calls to Jellyfin) is mocked.
If any of these tests fail, Jellyfin will show "no libraries" on fresh install.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, call

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


class TestConfigureLibrariesIntegration(unittest.TestCase):
    """End-to-end: configure-libraries must create 4 libraries in Jellyfin."""

    def test_job_does_not_skip(self):
        """The configure-libraries job must NOT return 'skipped'.

        This catches the exact bug where JELLYFIN_API_KEY is empty and
        the job silently returns without creating any libraries.
        """
        from media_stack.cli.commands.bootstrap_jobs import (
            JobContext, _run_media_server_handler,
        )

        ctx = JobContext()
        # Simulate having an API key (set by preflight before this job runs)
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-api-key"}):
            # Mock the actual HTTP handler since we can't reach Jellyfin
            with patch(
                "media_stack.services.apps.jellyfin.runtime_ops.ensure_jellyfin_libraries"
            ) as mock_ensure:
                result = _run_media_server_handler(ctx, "libraries", "Library")

        self.assertNotIn("skipped", result,
                         f"configure-libraries SKIPPED: {result}. "
                         "Libraries will NOT be created on fresh install.")
        mock_ensure.assert_called_once()

    def test_ensure_called_with_correct_config(self):
        """ensure_jellyfin_libraries must receive config with enabled libraries."""
        from media_stack.cli.commands.bootstrap_jobs import (
            JobContext, _run_media_server_handler,
        )

        captured_args = {}

        def capture_ensure(cfg, config_root, wait_timeout):
            captured_args["cfg"] = cfg
            captured_args["config_root"] = config_root
            captured_args["wait_timeout"] = wait_timeout

        ctx = JobContext()
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-api-key"}):
            with patch(
                "media_stack.services.apps.jellyfin.runtime_ops.ensure_jellyfin_libraries",
                side_effect=capture_ensure,
            ):
                _run_media_server_handler(ctx, "libraries", "Library")

        cfg = captured_args["cfg"]
        self.assertIn("jellyfin_libraries", cfg,
                       "cfg missing 'jellyfin_libraries' key — handler will silently return")
        libs_cfg = cfg["jellyfin_libraries"]
        self.assertTrue(libs_cfg.get("enabled"),
                        "jellyfin_libraries.enabled is False — handler will silently return")
        libs = libs_cfg.get("libraries", [])
        self.assertGreater(len(libs), 0,
                           "No libraries defined — nothing will be created")
        names = [lib.get("name") for lib in libs]
        self.assertIn("Movies", names, "Movies library not in config")
        self.assertIn("TV Shows", names, "TV Shows library not in config")

    def test_ensure_receives_api_key_in_config(self):
        """The libraries config must have api_key_env so ensure() can resolve credentials."""
        from media_stack.cli.commands.bootstrap_jobs import JobContext

        ctx = JobContext()
        libs_cfg = ctx.cfg.get("jellyfin_libraries", {})
        self.assertIn("api_key_env", libs_cfg,
                       "No api_key_env in libraries config — can't authenticate to Jellyfin")
        self.assertEqual(libs_cfg["api_key_env"], "JELLYFIN_API_KEY")


class TestConfigureLiveTvIntegration(unittest.TestCase):
    """End-to-end: configure-livetv must not skip."""

    def test_job_does_not_skip(self):
        from media_stack.cli.commands.bootstrap_jobs import (
            JobContext, _run_media_server_handler,
        )
        ctx = JobContext()
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-api-key"}):
            with patch(
                "media_stack.services.apps.jellyfin.runtime_ops.ensure_jellyfin_livetv"
            ) as mock_ensure:
                result = _run_media_server_handler(ctx, "livetv", "Live TV")
        self.assertNotIn("skipped", result, f"configure-livetv SKIPPED: {result}")
        mock_ensure.assert_called_once()


class TestConfigurePluginsIntegration(unittest.TestCase):
    """End-to-end: configure-plugins must not skip."""

    def test_job_does_not_skip(self):
        from media_stack.cli.commands.bootstrap_jobs import (
            JobContext, _run_media_server_handler,
        )
        ctx = JobContext()
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-api-key"}):
            with patch(
                "media_stack.services.apps.jellyfin.runtime_ops.ensure_jellyfin_plugins"
            ) as mock_ensure:
                result = _run_media_server_handler(ctx, "plugins", "Plugin")
        self.assertNotIn("skipped", result, f"configure-plugins SKIPPED: {result}")
        mock_ensure.assert_called_once()

    def test_plugins_config_has_install_list(self):
        from media_stack.cli.commands.bootstrap_jobs import JobContext
        ctx = JobContext()
        plugins_cfg = ctx.cfg.get("jellyfin_plugins", {})
        self.assertTrue(plugins_cfg.get("enabled"))
        install = plugins_cfg.get("install", [])
        self.assertGreater(len(install), 0, "No plugins to install")


class TestConfigurePlaybackIntegration(unittest.TestCase):
    """End-to-end: configure-playback must not skip."""

    def test_job_does_not_skip(self):
        from media_stack.cli.commands.bootstrap_jobs import (
            JobContext, _run_media_server_handler,
        )
        ctx = JobContext()
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-api-key"}):
            with patch(
                "media_stack.services.apps.jellyfin.runtime_ops.ensure_jellyfin_playback_defaults"
            ) as mock_ensure:
                result = _run_media_server_handler(ctx, "playback_defaults", "Playback")
        self.assertNotIn("skipped", result, f"configure-playback SKIPPED: {result}")
        mock_ensure.assert_called_once()


class TestJobSkipIsNotSilent(unittest.TestCase):
    """Verify that Job.run() does NOT report [OK] when a handler skips."""

    def test_skipped_job_logs_warning_not_ok(self):
        from media_stack.cli.commands.bootstrap_jobs import Job, JobContext
        import media_stack.services.runtime_platform as rp

        logged = []
        original_log = rp.log
        rp.log = lambda msg: logged.append(msg)
        try:
            job = Job("test-skip", lambda ctx: {"skipped": "no API key"})
            result = job.run(JobContext())
        finally:
            rp.log = original_log

        self.assertEqual(result["status"], "skipped")
        # Must NOT have [OK]
        ok_lines = [l for l in logged if "[OK] test-skip" in l]
        self.assertEqual(ok_lines, [],
                         f"Job logged [OK] despite being skipped: {ok_lines}")
        # Must have [WARN]
        warn_lines = [l for l in logged if "[WARN] test-skip" in l]
        self.assertGreater(len(warn_lines), 0,
                           "Job did not log [WARN] when skipped — failure is invisible")

    def test_skipped_sub_jobs_dont_run(self):
        """If a composite job is skipped, sub-jobs must NOT execute."""
        from media_stack.cli.commands.bootstrap_jobs import Job, JobContext

        sub_ran = []
        parent = Job("parent", lambda ctx: {"skipped": "no server"})
        parent.add_sub_job(Job("child", lambda ctx: sub_ran.append(True)))
        parent.run(JobContext())
        self.assertEqual(sub_ran, [],
                         "Sub-jobs ran despite parent being skipped")


class TestAllMediaServerJobsRun(unittest.TestCase):
    """Verify that run_all_media_server_jobs actually invokes every handler."""

    def test_all_jobs_dispatched(self):
        """Every discovered job must be dispatched by run_all_media_server_jobs."""
        from media_stack.cli.commands.bootstrap_jobs import (
            run_all_media_server_jobs, PREREQS, discover_jobs_from_contracts,
        )

        # Get expected job names from contracts
        expected_jobs = {j["name"] for j in discover_jobs_from_contracts()}

        # Mock all prereqs + all handlers
        handler_patches = {
            "ensure_jellyfin_libraries": MagicMock(),
            "ensure_jellyfin_livetv": MagicMock(),
            "ensure_jellyfin_plugins": MagicMock(),
            "ensure_jellyfin_playback_defaults": MagicMock(),
            "ensure_jellyfin_home_rails": MagicMock(),
            "ensure_jellyfin_auto_collections_config": MagicMock(),
            "ensure_jellyfin_prewarm": MagicMock(),
        }

        with patch.dict(PREREQS, {
            "media_server_reachable": lambda ctx: True,
            "media_server_api_key": lambda ctx: True,
            "media_server_id": lambda ctx: True,
        }):
            with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-api-key"}):
                with patch.multiple(
                    "media_stack.services.apps.jellyfin.runtime_ops",
                    **handler_patches,
                ):
                    result = run_all_media_server_jobs(max_wait=1)

        # Check that the runner dispatched jobs
        dispatched = set(result.get("jobs", {}).keys())
        for job_name in expected_jobs:
            self.assertIn(job_name, dispatched,
                          f"Job '{job_name}' was not dispatched. Dispatched: {sorted(dispatched)}")


if __name__ == "__main__":
    unittest.main()
