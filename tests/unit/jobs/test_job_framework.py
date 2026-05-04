"""Tests for bootstrap job decomposition framework."""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.jobs.framework import (  # noqa: E402
    Job, JobContext, build_job_framework, get_job_registry, run_job,
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
        # The registry must include every per-app job. After the
        # action→job migration it also includes the eight migrated
        # core jobs (envoy-config, validate-credentials, post-setup,
        # restart-apps, discover-indexers, push-indexers,
        # discover-api-keys, run-legacy-pipeline). Use subset
        # semantics so adding new contract jobs doesn't break this.
        per_app = {
            "configure-libraries", "configure-livetv",
            "configure-plugins", "configure-playback",
            "configure-home-screen", "configure-collections",
            "refresh-media", "configure-categories",
            "configure-jellyseerr", "configure-arr-clients",
            "configure-indexers", "configure-auth",
            "configure-auto-scan",
        }
        core_migrated = {
            "envoy-config", "validate-credentials", "post-setup",
            "restart-apps", "discover-indexers", "push-indexers",
            "discover-api-keys", "run-legacy-pipeline",
        }
        self.assertTrue(
            per_app.issubset(set(registry.keys())),
            f"per-app jobs missing from registry: "
            f"{per_app - set(registry.keys())}",
        )
        self.assertTrue(
            core_migrated.issubset(set(registry.keys())),
            f"core-migrated jobs missing from registry: "
            f"{core_migrated - set(registry.keys())}",
        )

    def test_all_handlers_callable(self):
        for name, handler in get_job_registry().items():
            self.assertTrue(callable(handler), f"{name} is not callable")


class TestBuildBootstrapJobs(unittest.TestCase):
    def test_root_job_is_bootstrap(self):
        root = build_job_framework()
        self.assertEqual(root.name, "bootstrap")

    def test_has_media_server_sub_job(self):
        root = build_job_framework()
        names = [j.name for j in root.sub_jobs]
        self.assertIn("configure-media-server", names)

    def test_media_server_has_all_sub_jobs(self):
        root = build_job_framework()
        ms = next(j for j in root.sub_jobs if j.name == "configure-media-server")
        sub_names = [j.name for j in ms.sub_jobs]
        self.assertIn("configure-libraries", sub_names)
        self.assertIn("configure-livetv", sub_names)
        self.assertIn("configure-plugins", sub_names)
        self.assertIn("configure-playback", sub_names)
        self.assertIn("configure-home-screen", sub_names)
        # ``configure-collections`` was here pre-ADR-0005 Phase 3.
        # The cutover dropped its ``phase: media_server`` so the
        # bootstrap DAG no longer schedules it via the framework —
        # the orchestrator dispatches Maintainerr's rule-link promise
        # via ``MaintainerrLifecycle.ensure_rules_linked_to_arr``
        # instead. The job stays REGISTERED for ``run_job(name)``
        # auto-heal + Jellyfin auto-collections plugin reconcile. See
        # tests/unit/contracts/test_maintainerr_rules_promise_driven.py.
        self.assertNotIn(
            "configure-collections", sub_names,
            "configure-collections regained ``phase: media_server`` — "
            "the ADR-0005 Phase 3 cutover removed it. Reverting means "
            "restoring phase + priority in jellyfin.yaml AND flipping "
            "the maintainerr-rules-linked-to-arr promise back to "
            "string ``ensured_by: configure-collections``.",
        )

    def test_has_prewarm_in_tree(self):
        root = build_job_framework()
        from media_stack.services.jobs.framework import _find_job_in_tree
        prewarm = _find_job_in_tree(root, "refresh-media")
        self.assertIsNotNone(prewarm, "refresh-media not found in tree")


class TestRunJob(unittest.TestCase):
    def test_unknown_job_returns_error(self):
        result = run_job("nonexistent-job-xyz")
        self.assertIn("error", result)
        self.assertIn("known", result)

    def test_run_job_calls_handler(self):
        from media_stack.services.jobs.framework import PREREQS
        with patch.dict(PREREQS, {
            "media_server_reachable": lambda ctx: True,
            "media_server_api_key": lambda ctx: True,
        }):
            with patch(
                "media_stack.services.apps.jellyfin.runtime_ops.ensure_jellyfin_libraries"
            ) as mock_handler:
                result = run_job("configure-libraries")
        mock_handler.assert_called_once()


class TestCfgFromContracts(unittest.TestCase):
    """Verify JobContext.cfg loads from service YAML contracts with flat keys."""

    def test_cfg_has_jellyfin_flat_keys(self):
        """Jellyfin service YAML defaults produce flat keys (jellyfin_libraries, etc.)."""
        ctx = JobContext()
        cfg = ctx.cfg
        expected = [
            "jellyfin_libraries", "jellyfin_livetv", "jellyfin_plugins",
            "jellyfin_playback", "jellyfin_prewarm", "jellyfin_home_rails",
            "jellyfin_auto_collections",
        ]
        for key in expected:
            self.assertIn(key, cfg, f"cfg missing {key} — handler will silently skip")

    def test_cfg_jellyfin_libraries_has_library_list(self):
        ctx = JobContext()
        libs = ctx.cfg.get("jellyfin_libraries", {})
        self.assertIn("libraries", libs, "jellyfin_libraries must have 'libraries' list")
        self.assertIsInstance(libs["libraries"], list)
        self.assertGreater(len(libs["libraries"]), 0)

    def test_cfg_bazarr_is_not_flattened(self):
        """Services with scalar defaults keep their service-id form."""
        ctx = JobContext()
        cfg = ctx.cfg
        self.assertIn("bazarr", cfg)
        self.assertNotIn("bazarr_enabled", cfg)
        self.assertNotIn("bazarr_url", cfg)

    def test_cfg_jellyfin_nested_key_not_present(self):
        """No nested 'jellyfin' key — only flat jellyfin_* keys."""
        ctx = JobContext()
        cfg = ctx.cfg
        # Should NOT have a single "jellyfin" dict with sub-sections
        if "jellyfin" in cfg:
            # If present, it should not have the sub-section keys
            self.assertNotIn("libraries", cfg["jellyfin"])

    def test_cfg_with_profile_bindings(self):
        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}
        ctx._cfg_cache = None  # force reload
        cfg = ctx.cfg
        self.assertIn("technology_bindings", cfg)
        self.assertEqual(cfg["technology_bindings"]["media_server"], "jellyfin")


class TestHandlerReceivesFlatKeys(unittest.TestCase):
    """Verify _run_media_server_handler passes flat config to handlers."""

    @patch("media_stack.services.jobs.framework._ensure_media_server_api_key")
    def test_handler_receives_flat_keys(self, mock_ensure_key):
        captured_cfg = {}
        def fake_ensure(cfg, config_root, wait_timeout):
            captured_cfg.update(cfg)

        ctx = JobContext()
        ctx._profile_cache = {"technology_bindings": {"media_server": "jellyfin"}}

        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-key"}):
            with patch("importlib.import_module") as mock_import:
                mock_mod = MagicMock()
                mock_mod.ensure_jellyfin_libraries = fake_ensure
                mock_import.return_value = mock_mod
                from media_stack.services.jobs.framework import _run_media_server_handler
                _run_media_server_handler(ctx, "libraries", "Library")

        self.assertIn("jellyfin_libraries", captured_cfg)
        self.assertIsInstance(captured_cfg["jellyfin_libraries"], dict)


class TestMediaServerJobsNotSkipped(unittest.TestCase):
    """Verify that media server jobs actually run — not silently skipped.

    This catches the exact bug where Jellyfin shows 'no libraries created'
    after a fresh install because media_server_id() returned empty.
    """

    def test_media_server_id_never_empty(self):
        """media_server_id() must return a service ID even without profile env."""
        ctx = JobContext()
        ms_id = ctx.media_server_id()
        self.assertTrue(
            ms_id,
            "media_server_id() returned empty — ALL media server jobs will silently skip. "
            "technology_bindings must be derivable from service YAML capabilities."
        )

    def test_technology_bindings_derived_from_capabilities(self):
        """technology_bindings must be in cfg even without BOOTSTRAP_PROFILE_FILE."""
        ctx = JobContext()
        bindings = ctx.cfg.get("technology_bindings", {})
        self.assertIn("media_server", bindings,
                       "No media_server binding — derived from jellyfin.yaml capabilities")
        self.assertEqual(bindings["media_server"], "jellyfin")

    def test_configure_libraries_not_skipped(self):
        """The configure-libraries job must NOT return 'skipped'."""
        from media_stack.services.jobs.framework import _run_media_server_handler
        ctx = JobContext()
        ctx._profile_cache = {}
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": "test-key"}):
            with patch("importlib.import_module") as mock_import:
                mock_mod = MagicMock()
                mock_mod.ensure_jellyfin_libraries = MagicMock()
                mock_import.return_value = mock_mod
                result = _run_media_server_handler(ctx, "libraries", "Library")
        self.assertNotIn("skipped", result,
                         f"configure-libraries was SKIPPED: {result}. "
                         "Libraries will NOT be created on fresh install.")

    def test_cfg_has_libraries_with_entries(self):
        """jellyfin_libraries must have at least one library defined."""
        ctx = JobContext()
        libs_cfg = ctx.cfg.get("jellyfin_libraries", {})
        self.assertTrue(libs_cfg.get("enabled"), "jellyfin_libraries.enabled must be True")
        libs = libs_cfg.get("libraries", [])
        self.assertGreater(len(libs), 0, "No libraries defined — nothing will be created")
        names = [lib.get("name") for lib in libs]
        self.assertIn("Movies", names)
        self.assertIn("TV Shows", names)

    def test_all_media_server_jobs_have_handler(self):
        """Every discovered job must have a resolvable handler."""
        from media_stack.services.jobs.framework import discover_jobs_from_contracts
        import importlib
        for job in discover_jobs_from_contracts():
            handler_path = job.get("handler", "")
            if not handler_path:
                continue
            if ":" in handler_path:
                mod_path, fn_name = handler_path.rsplit(":", 1)
            else:
                mod_path, fn_name = handler_path.rsplit(".", 1)
            try:
                mod = importlib.import_module(mod_path)
                fn = getattr(mod, fn_name, None)
                self.assertIsNotNone(fn,
                    f"Job '{job['name']}' handler {handler_path} — function not found")
            except ImportError:
                self.fail(f"Job '{job['name']}' handler {handler_path} — module not found")


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
