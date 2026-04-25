import argparse
import sys
import unittest
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.jobs.action_handlers import (  # noqa: E402
    action_discover_indexers,
    action_bootstrap,
    action_envoy_config,
    action_post_setup,
    action_reconcile,
    action_restart_apps,
    action_push_indexers,
)

LOG_PATH = "media_stack.services.jobs.action_handlers.runtime_platform.log"


def _ns(**kw):
    """Build an argparse.Namespace with sensible defaults."""
    return argparse.Namespace(**kw)


class ActionBootstrapTests(unittest.TestCase):
    """Tests for action_bootstrap."""

    @mock.patch.dict(
        "os.environ",
        {"BOOTSTRAP_RUN_PREFLIGHTS": "1", "BOOTSTRAP_SKIP_JOBS_FRAMEWORK": "1"},
        clear=False,
    )
    @mock.patch(LOG_PATH)
    def test_preflights_run_when_enabled(self, mock_log):
        state = MagicMock()
        args = _ns()
        run_preflights = MagicMock()
        persist_keys = MagicMock()
        runner = MagicMock()
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))

        action_bootstrap(args, state, run_preflights, persist_keys, build_runner)

        run_preflights.assert_called_once_with(state, args)
        persist_keys.assert_called_once_with(state)
        build_runner.assert_called_once_with(args)
        runner.run.assert_called_once_with(runtime_state)
        mock_log.assert_called_with("[OK] Bootstrap completed successfully")

    @mock.patch.dict(
        "os.environ",
        {"BOOTSTRAP_RUN_PREFLIGHTS": "0", "BOOTSTRAP_SKIP_JOBS_FRAMEWORK": "1"},
        clear=False,
    )
    @mock.patch(LOG_PATH)
    def test_preflights_skipped_when_disabled(self, mock_log):
        state = MagicMock()
        args = _ns()
        run_preflights = MagicMock()
        persist_keys = MagicMock()
        runner = MagicMock()
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))

        action_bootstrap(args, state, run_preflights, persist_keys, build_runner)

        run_preflights.assert_not_called()
        persist_keys.assert_not_called()
        runner.run.assert_called_once_with(runtime_state)


class ActionFinalizeTests(unittest.TestCase):
    """Tests for action_post_setup."""

    @mock.patch(LOG_PATH)
    def test_finalize_success(self, mock_log):
        args = _ns()
        state = MagicMock()
        runner = MagicMock()
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))
        run_post_bootstrap = MagicMock()

        action_post_setup(args, state, build_runner, run_post_bootstrap)

        runner._run_post_servarr_steps.assert_called_once_with(runtime_state)
        run_post_bootstrap.assert_called_once_with(state, args)
        mock_log.assert_called_with("[OK] Finalize completed")

    @mock.patch(LOG_PATH)
    def test_finalize_post_servarr_exception_logged_and_continues(self, mock_log):
        args = _ns()
        state = MagicMock()
        runner = MagicMock()
        runner._run_post_servarr_steps.side_effect = RuntimeError("boom")
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))
        run_post_bootstrap = MagicMock()

        action_post_setup(args, state, build_runner, run_post_bootstrap)

        # Post-bootstrap still runs despite the exception.
        run_post_bootstrap.assert_called_once_with(state, args)
        # The warning was logged.
        warn_calls = [c for c in mock_log.call_args_list if "[WARN]" in str(c)]
        self.assertTrue(len(warn_calls) >= 1, "Expected a WARN log for post-servarr failure")


class ActionAutoIndexersTests(unittest.TestCase):
    """Tests for action_discover_indexers."""

    @mock.patch(LOG_PATH)
    def test_runs_indexer_steps_phase(self, mock_log):
        args = _ns()
        runner = MagicMock()
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))

        action_discover_indexers(args, build_runner)

        build_runner.assert_called_once_with(args, auto_prowlarr_indexers=True)
        runner._run_runner_plan_phase.assert_called_once_with(runtime_state, "indexer_steps")
        mock_log.assert_called_with("[OK] Auto-indexer discovery complete")

    @mock.patch(LOG_PATH)
    def test_falls_back_to_full_pipeline_on_phase_error(self, mock_log):
        args = _ns()
        runner = MagicMock()
        runner._run_runner_plan_phase.side_effect = AttributeError("no such phase")
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))

        action_discover_indexers(args, build_runner)

        runner.run.assert_called_once_with(runtime_state)
        warn_calls = [c for c in mock_log.call_args_list if "full pipeline" in str(c)]
        self.assertTrue(len(warn_calls) >= 1)


class ActionRestartAppsTests(unittest.TestCase):
    """Tests for action_restart_apps."""

    @mock.patch(LOG_PATH)
    def test_runs_restart_specs(self, mock_log):
        args = _ns()
        state = MagicMock()
        restart_spec = {"name": "restart_apps", "handler": "some_handler"}
        other_spec = {"name": "other_thing", "handler": "other"}
        load_handler_specs = MagicMock(return_value=[other_spec, restart_spec])
        run_handler_specs = MagicMock()

        action_restart_apps(args, state, load_handler_specs, run_handler_specs)

        load_handler_specs.assert_called_once_with("container_post_setup_handlers")
        run_handler_specs.assert_called_once_with(
            [restart_spec], state, args, phase_label="RESTART"
        )

    @mock.patch(LOG_PATH)
    def test_no_op_when_no_restart_spec_found(self, mock_log):
        args = _ns()
        state = MagicMock()
        load_handler_specs = MagicMock(return_value=[{"name": "unrelated"}])
        run_handler_specs = MagicMock()

        action_restart_apps(args, state, load_handler_specs, run_handler_specs)

        run_handler_specs.assert_not_called()
        mock_log.assert_called_with(
            "[INFO] restart-apps: nothing to do (blanket restarts intentionally disabled)"
        )


class ActionSyncIndexersTests(unittest.TestCase):
    """Tests for action_push_indexers."""

    @mock.patch(LOG_PATH)
    def test_sync_indexers_success(self, mock_log):
        args = _ns()
        runner = MagicMock()
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))

        action_push_indexers(args, build_runner)

        runner._run_runner_plan_phase.assert_called_once_with(runtime_state, "indexer_steps")
        mock_log.assert_called_with("[OK] Indexer sync complete")

    @mock.patch(LOG_PATH)
    def test_sync_indexers_logs_warning_on_exception(self, mock_log):
        args = _ns()
        runner = MagicMock()
        runner._run_runner_plan_phase.side_effect = RuntimeError("connection refused")
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))

        action_push_indexers(args, build_runner)

        warn_calls = [c for c in mock_log.call_args_list if "[WARN]" in str(c)]
        self.assertTrue(len(warn_calls) >= 1)
        # Still logs OK at the end.
        mock_log.assert_called_with("[OK] Indexer sync complete")


class ActionEnvoyConfigTests(unittest.TestCase):
    """Tests for action_envoy_config."""

    @mock.patch(LOG_PATH)
    def test_config_root_defaults_when_unset(self, mock_log):
        """CONFIG_ROOT should default to /srv-config when not already set."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock()
        # Start with CONFIG_ROOT absent; the function should set it.
        env = {"K8S_NAMESPACE": ""}
        with (
            mock.patch.dict("os.environ", env, clear=True),
            mock.patch.dict(
                "sys.modules",
                {"media_stack.services.edge.envoy_config_generator": gen_mod},
            ),
        ):
            action_envoy_config(_ns())
            import os
            self.assertEqual(os.environ.get("CONFIG_ROOT"), "/srv-config")

    @mock.patch(LOG_PATH)
    def test_config_root_preserved_when_already_set(self, mock_log):
        """CONFIG_ROOT should not be overwritten if already set."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock()
        with (
            mock.patch.dict(
                "os.environ",
                {"CONFIG_ROOT": "/custom/root", "K8S_NAMESPACE": ""},
                clear=False,
            ),
            mock.patch.dict(
                "sys.modules",
                {"media_stack.services.edge.envoy_config_generator": gen_mod},
            ),
        ):
            import os
            action_envoy_config(_ns())
            self.assertEqual(os.environ["CONFIG_ROOT"], "/custom/root")

    @mock.patch(LOG_PATH)
    def test_system_exit_nonzero_reports_error_and_returns(self, mock_log):
        """SystemExit with non-zero code should log an error and skip restart."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock(side_effect=SystemExit(1))
        with (
            mock.patch.dict(
                "os.environ", {"CONFIG_ROOT": "/x", "K8S_NAMESPACE": ""}, clear=False
            ),
            mock.patch.dict(
                "sys.modules",
                {"media_stack.services.edge.envoy_config_generator": gen_mod},
            ),
        ):
            action_envoy_config(_ns())

        error_calls = [c for c in mock_log.call_args_list if "[ERROR]" in str(c)]
        self.assertTrue(len(error_calls) >= 1, "Expected an ERROR log on non-zero exit")
        # Should NOT log "[OK] Envoy config written" when generation failed.
        ok_config_calls = [c for c in mock_log.call_args_list if "Envoy config written" in str(c)]
        self.assertEqual(len(ok_config_calls), 0)

    @mock.patch(LOG_PATH)
    def test_system_exit_zero_treated_as_success(self, mock_log):
        """SystemExit(0) means success; envoy restart should proceed."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock(side_effect=SystemExit(0))
        docker_mod = MagicMock()
        container = MagicMock()
        docker_mod.from_env.return_value.containers.get.return_value = container
        with (
            mock.patch.dict(
                "os.environ", {"CONFIG_ROOT": "/x", "K8S_NAMESPACE": ""}, clear=False
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "media_stack.services.edge.envoy_config_generator": gen_mod,
                    "docker": docker_mod,
                },
            ),
        ):
            action_envoy_config(_ns())

        ok_calls = [c for c in mock_log.call_args_list if "Envoy config written" in str(c)]
        self.assertTrue(len(ok_calls) >= 1, "Expected '[OK] Envoy config written'")

    @mock.patch(LOG_PATH)
    def test_docker_restart_on_empty_k8s_namespace(self, mock_log):
        """When K8S_NAMESPACE is empty, should restart via Docker."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock()
        docker_mod = MagicMock()
        container = MagicMock()
        docker_mod.from_env.return_value.containers.get.return_value = container
        with (
            mock.patch.dict(
                "os.environ", {"CONFIG_ROOT": "/x", "K8S_NAMESPACE": ""}, clear=False
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "media_stack.services.edge.envoy_config_generator": gen_mod,
                    "docker": docker_mod,
                },
            ),
        ):
            action_envoy_config(_ns())

        container.restart.assert_called_once_with(timeout=10)
        docker_calls = [c for c in mock_log.call_args_list if "Docker" in str(c)]
        self.assertTrue(len(docker_calls) >= 1)

    @mock.patch(LOG_PATH)
    def test_docker_container_not_found_warns(self, mock_log):
        """When the envoy container doesn't exist, should log a warning."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock()
        docker_mod = MagicMock()
        docker_mod.from_env.return_value.containers.get.side_effect = Exception("not found")
        with (
            mock.patch.dict(
                "os.environ", {"CONFIG_ROOT": "/x", "K8S_NAMESPACE": ""}, clear=False
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "media_stack.services.edge.envoy_config_generator": gen_mod,
                    "docker": docker_mod,
                },
            ),
        ):
            action_envoy_config(_ns())

        warn_calls = [c for c in mock_log.call_args_list if "not found" in str(c)]
        self.assertTrue(len(warn_calls) >= 1)

    @mock.patch(LOG_PATH)
    def test_k8s_restart_deletes_envoy_pods(self, mock_log):
        """When K8S_NAMESPACE is set, should delete envoy pods via k8s API."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock()

        # Build a fake k8s environment.
        k8s_client_mod = MagicMock()
        k8s_config_mod = MagicMock()

        fake_pod = MagicMock()
        fake_pod.metadata.name = "envoy-abc123"
        v1 = MagicMock()
        v1.list_namespaced_pod.return_value.items = [fake_pod]
        k8s_client_mod.CoreV1Api.return_value = v1

        # Create a module-like mock that has both client and config attributes.
        k8s_mod = MagicMock()
        k8s_mod.client = k8s_client_mod
        k8s_mod.config = k8s_config_mod

        with (
            mock.patch.dict(
                "os.environ",
                {"CONFIG_ROOT": "/x", "K8S_NAMESPACE": "media-stack"},
                clear=False,
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "media_stack.services.edge.envoy_config_generator": gen_mod,
                    "kubernetes": k8s_mod,
                    "kubernetes.client": k8s_client_mod,
                    "kubernetes.config": k8s_config_mod,
                },
            ),
        ):
            action_envoy_config(_ns())

        v1.delete_namespaced_pod.assert_called_once_with(
            name="envoy-abc123", namespace="media-stack"
        )
        k8s_calls = [c for c in mock_log.call_args_list if "K8s" in str(c)]
        self.assertTrue(len(k8s_calls) >= 1)

    @mock.patch(LOG_PATH)
    def test_restart_outer_exception_logs_warning(self, mock_log):
        """When the entire restart block throws, log a WARN and continue."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock()
        # Make docker import raise so the outer except catches it.
        bad_docker = MagicMock()
        bad_docker.from_env.side_effect = OSError("socket missing")
        with (
            mock.patch.dict(
                "os.environ", {"CONFIG_ROOT": "/x", "K8S_NAMESPACE": ""}, clear=False
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "media_stack.services.edge.envoy_config_generator": gen_mod,
                    "docker": bad_docker,
                },
            ),
        ):
            action_envoy_config(_ns())

        skip_calls = [c for c in mock_log.call_args_list if "restart skipped" in str(c)]
        self.assertTrue(len(skip_calls) >= 1, "Expected WARN about restart skipped")

    @mock.patch(LOG_PATH)
    def test_system_exit_none_treated_as_success(self, mock_log):
        """SystemExit(None) has a falsy code and should be treated as success."""
        gen_mod = MagicMock()
        gen_mod.main = MagicMock(side_effect=SystemExit(None))
        docker_mod = MagicMock()
        docker_mod.from_env.return_value.containers.get.return_value = MagicMock()
        with (
            mock.patch.dict(
                "os.environ", {"CONFIG_ROOT": "/x", "K8S_NAMESPACE": ""}, clear=False
            ),
            mock.patch.dict(
                "sys.modules",
                {
                    "media_stack.services.edge.envoy_config_generator": gen_mod,
                    "docker": docker_mod,
                },
            ),
        ):
            action_envoy_config(_ns())

        ok_calls = [c for c in mock_log.call_args_list if "Envoy config written" in str(c)]
        self.assertTrue(len(ok_calls) >= 1, "Expected success when exit code is None")
        error_calls = [c for c in mock_log.call_args_list if "[ERROR]" in str(c)]
        self.assertEqual(len(error_calls), 0, "Should have no ERROR for exit code None")


class ActionReconcileTests(unittest.TestCase):
    """Tests for action_reconcile."""

    @mock.patch(LOG_PATH)
    def test_reconcile_runs_full_pipeline(self, mock_log):
        args = _ns()
        runner = MagicMock()
        runtime_state = MagicMock()
        build_runner = MagicMock(return_value=(runner, runtime_state))

        action_reconcile(args, build_runner)

        build_runner.assert_called_once_with(args)
        runner.run.assert_called_once_with(runtime_state)
        mock_log.assert_called_with("[OK] Reconcile complete")


if __name__ == "__main__":
    unittest.main()
