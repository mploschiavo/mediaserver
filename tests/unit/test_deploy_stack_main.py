"""Unit tests for deploy_stack_main module."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.deploy_cli_config_service import DeployStackConfig  # noqa: E402
from media_stack.cli.commands.deploy_stack_main import (  # noqa: E402
    DeployError,
    DeployStackRunner,
    SkipPhase,
    _MIN_STACK_DISK_ALLOCATION_GB,
    main,
)


def _make_config(
    root_dir: Path,
    *,
    adapter_hooks: dict | None = None,
    technology_bindings: dict | None = None,
    extra_payload: dict | None = None,
    profile: str = "full",
    platform_target: str = "compose",
    namespace: str = "media-stack",
    ingress_domain: str = "local",
    auth_provider: str = "none",
    run_bootstrap: str = "0",
    route_strategy: str = "subdomain",
    bootstrap_runner_image: str = "registry/image:latest",
    storage_mode: str = "dynamic-pvc",
    disk_allocation_gb: int = 500,
    network_cidr: str = "192.168.1.0/24",
    chaos_enabled: str = "0",
    chaos_actions: str = "restart_container",
    chaos_duration_minutes: int = 5,
    chaos_interval_seconds: int = 60,
    edge_router_provider: str = "",
    selected_apps: str = "",
    delete_namespace: str = "0",
    delete_namespace_confirm: str = "",
    compose_project_name: str = "",
    compose_profiles: str = "",
    run_smoke_test: str = "1",
) -> DeployStackConfig:
    """Build a DeployStackConfig backed by a temporary bootstrap JSON file."""
    payload: dict = {}
    if adapter_hooks is not None:
        payload["adapter_hooks"] = adapter_hooks
    if technology_bindings is not None:
        payload["technology_bindings"] = technology_bindings
    if extra_payload:
        payload.update(extra_payload)
    config_file = root_dir / "bootstrap-config.json"
    config_file.write_text(json.dumps(payload), encoding="utf-8")
    return DeployStackConfig(
        root_dir=root_dir,
        platform_target=platform_target,
        namespace=namespace,
        ingress_domain=ingress_domain,
        config_file=config_file,
        auth_provider=auth_provider,
        run_bootstrap=run_bootstrap,
        route_strategy=route_strategy,
        bootstrap_runner_image=bootstrap_runner_image,
        profile=profile,
        storage_mode=storage_mode,
        disk_allocation_gb=disk_allocation_gb,
        network_cidr=network_cidr,
        chaos_enabled=chaos_enabled,
        chaos_actions=chaos_actions,
        chaos_duration_minutes=chaos_duration_minutes,
        chaos_interval_seconds=chaos_interval_seconds,
        edge_router_provider=edge_router_provider,
        selected_apps=selected_apps,
        delete_namespace=delete_namespace,
        delete_namespace_confirm=delete_namespace_confirm,
        compose_project_name=compose_project_name,
        compose_profiles=compose_profiles,
        run_smoke_test=run_smoke_test,
    )


def _runner(cfg: DeployStackConfig, **kwargs) -> DeployStackRunner:
    return DeployStackRunner(cfg=cfg, info_fn=lambda _: None, **kwargs)


class TestDeployStackMain(unittest.TestCase):
    """40 unit tests covering deploy_stack_main module."""

    # 1
    def test_deploy_error_is_runtime_error(self):
        err = DeployError("something broke")
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(str(err), "something broke")

    # 2
    def test_skip_phase_is_runtime_error(self):
        err = SkipPhase("skip reason")
        self.assertIsInstance(err, RuntimeError)

    # 3
    def test_resolved_config_loads_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), adapter_hooks={"edge": {}})
            runner = _runner(cfg)
            result1 = runner._resolved_bootstrap_config()
            result2 = runner._resolved_bootstrap_config()
            self.assertIs(result1, result2)
            self.assertIn("adapter_hooks", result1)

    # 4
    def test_resolved_config_raises_on_non_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_file = root / "bad.json"
            config_file.write_text('"just a string"', encoding="utf-8")
            cfg = _make_config(root)
            cfg.config_file = config_file
            runner = _runner(cfg)
            with self.assertRaises(DeployError) as ctx:
                runner._resolved_bootstrap_config()
            self.assertIn("Expected JSON object", str(ctx.exception))

    # 5
    def test_is_truthy_recognizes_truthy_and_falsy(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            for val in ("1", "true", "TRUE", "yes", "on", "y"):
                self.assertTrue(runner._is_truthy(val), f"{val!r} should be truthy")
            for val in ("0", "false", "no", "off", "", "nope"):
                self.assertFalse(runner._is_truthy(val), f"{val!r} should be falsy")

    # 6
    def test_compose_profiles_empty_and_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner_empty = _runner(_make_config(Path(tmp), compose_profiles=""))
            self.assertEqual(runner_empty._compose_profiles(), ())
            runner_csv = _runner(_make_config(Path(tmp), compose_profiles="gpu,monitoring"))
            self.assertEqual(runner_csv._compose_profiles(), ("gpu", "monitoring"))

    # 7
    @patch(
        "media_stack.cli.commands.deploy_stack_main.compose_service_names_by_provider",
        return_value={"authelia": ("authelia",)},
    )
    def test_selected_apps_injects_auth_provider_services(self, _mock):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), selected_apps="sonarr,radarr", auth_provider="authelia")
            runner = _runner(cfg)
            result = runner._selected_apps()
            self.assertIn("sonarr", result)
            self.assertIn("authelia", result)

    # 8
    def test_chaos_actions_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp))
            cfg.chaos_actions = "restart,restart,pause"
            runner = _runner(cfg)
            self.assertEqual(runner._chaos_actions(), ("restart", "pause"))

    # 9
    def test_edge_router_provider_explicit_overrides_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp),
                adapter_hooks={"edge": {"router_provider": "traefik"}},
                edge_router_provider="envoy",
            )
            runner = _runner(cfg)
            self.assertEqual(runner._edge_router_provider(), "envoy")

    # 10
    def test_edge_router_provider_falls_back_to_hook(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp),
                adapter_hooks={"edge": {"router_provider": "traefik"}},
            )
            runner = _runner(cfg)
            self.assertEqual(runner._edge_router_provider(), "traefik")

    # 11
    def test_bootstrap_job_hooks_present_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            hooks = {"runtime_config_policy_handler": "mod:Handler"}
            cfg = _make_config(Path(tmp), adapter_hooks={"bootstrap_job": hooks})
            runner = _runner(cfg)
            self.assertEqual(runner._bootstrap_job_hooks(), hooks)
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp))
            runner = _runner(cfg)
            self.assertEqual(runner._bootstrap_job_hooks(), {})

    # 12
    def test_edge_hooks_present_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), adapter_hooks={"edge": {"router_provider": "envoy"}})
            runner = _runner(cfg)
            self.assertEqual(runner._edge_hooks()["router_provider"], "envoy")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), adapter_hooks={"bootstrap_job": {}})
            runner = _runner(cfg)
            self.assertEqual(runner._edge_hooks(), {})

    # 13
    def test_ingress_class_priority_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp),
                adapter_hooks={"edge": {"ingress_class_priority": ["nginx", "traefik", "nginx"]}},
            )
            runner = _runner(cfg)
            self.assertEqual(runner._ingress_class_priority(), ("nginx", "traefik"))

    # 14
    def test_compose_passthrough_env_vars_always_includes_admin_creds(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            result = runner._compose_passthrough_env_vars()
            self.assertIn("STACK_ADMIN_USERNAME", result)
            self.assertIn("STACK_ADMIN_PASSWORD", result)

    # 15
    def test_compose_passthrough_env_vars_includes_secret_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp),
                adapter_hooks={
                    "bootstrap_job": {
                        "secret_priming_targets": {
                            "jellyfin_key": {"env_var": "JELLYFIN_API_KEY"},
                        }
                    }
                },
            )
            runner = _runner(cfg)
            self.assertIn("JELLYFIN_API_KEY", runner._compose_passthrough_env_vars())

    # 16
    def test_policy_handler_spec_valid_and_rejects_without_colon(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp),
                adapter_hooks={"bootstrap_job": {"runtime_config_policy_handler": "mod:Handler"}},
            )
            runner = _runner(cfg)
            self.assertEqual(runner._runtime_config_policy_handler_spec(), "mod:Handler")
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp),
                adapter_hooks={"bootstrap_job": {"runtime_config_policy_handler": "nocolon"}},
            )
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._runtime_config_policy_handler_spec()

    # 17
    def test_validate_rejects_missing_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp))
            cfg.config_file = Path(tmp) / "nonexistent.json"
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._validate_inputs()

    # 18
    def test_validate_rejects_empty_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), namespace="  ")
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._validate_inputs()

    # 19
    def test_validate_rejects_empty_ingress_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), ingress_domain="  ")
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._validate_inputs()

    # 20
    def test_validate_rejects_invalid_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), profile="bogus")
            runner = _runner(cfg)
            with self.assertRaises(DeployError) as ctx:
                runner._validate_inputs()
            self.assertIn("Unknown PROFILE", str(ctx.exception))

    # 21
    def test_validate_rejects_small_disk_allocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), disk_allocation_gb=5)
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._validate_inputs()

    # 22
    def test_validate_rejects_invalid_network_cidr(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), network_cidr="not-a-cidr")
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._validate_inputs()

    # 23
    def test_validate_rejects_public_cidr(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), network_cidr="8.8.8.0/24")
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._validate_inputs()

    # 24
    def test_validate_rejects_empty_runner_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), bootstrap_runner_image="  ")
            runner = _runner(cfg)
            with self.assertRaises(DeployError):
                runner._validate_inputs()

    # 25
    def test_validate_rejects_chaos_enabled_without_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp), chaos_enabled="1", chaos_actions="")
            runner = _runner(cfg)
            with self.assertRaises(DeployError) as ctx:
                runner._validate_inputs()
            self.assertIn("CHAOS_ACTIONS", str(ctx.exception))

    # 26
    def test_validate_passes_with_valid_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(Path(tmp))
            runner = _runner(cfg)
            runner._validate_inputs()  # should not raise

    # 27
    def test_run_phase_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            called = []
            runner._run_phase("p1", lambda: called.append(True))
            self.assertTrue(called)
            self.assertEqual(runner.tracker.results[-1], "ok")

    # 28
    def test_run_phase_disabled_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            runner._run_phase("p2", lambda: None, enabled=False)
            self.assertEqual(runner.tracker.results[-1], "skipped")

    # 29
    def test_run_phase_skip_phase_marks_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))

            def raise_skip():
                raise SkipPhase()

            runner._run_phase("p3", raise_skip)
            self.assertEqual(runner.tracker.results[-1], "skipped")

    # 30
    def test_run_phase_exception_marks_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))

            def raise_err():
                raise RuntimeError("boom")

            with self.assertRaises(RuntimeError):
                runner._run_phase("p4", raise_err)
            self.assertEqual(runner.tracker.results[-1], "failed")

    # 31
    def test_delete_env_disabled_when_not_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp), delete_namespace="0"))
            self.assertFalse(runner._delete_environment_enabled())

    # 32
    def test_delete_env_enabled_with_i_understand(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp), delete_namespace="1", delete_namespace_confirm="I_UNDERSTAND"
            )
            runner = _runner(cfg)
            self.assertTrue(runner._delete_environment_enabled())

    # 33
    def test_delete_confirmation_target_compose_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_config(
                Path(tmp), platform_target="compose", compose_project_name="proj"
            )
            runner = _runner(cfg)
            self.assertEqual(runner._delete_environment_confirmation_target(), "proj")

    # 34
    def test_is_k8s_apply_with_stdin(self):
        self.assertTrue(DeployStackRunner._is_k8s_apply_with_stdin(["apply", "-f", "-"]))
        self.assertFalse(DeployStackRunner._is_k8s_apply_with_stdin(["get", "-f", "-"]))
        self.assertFalse(DeployStackRunner._is_k8s_apply_with_stdin(["apply", "-"]))

    # 35
    def test_run_kubectl_raises_without_kube_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            runner.kube = None
            with self.assertRaises(DeployError):
                runner._run_kubectl(["get", "pods"])

    # 36
    @patch("media_stack.cli.commands.deploy_stack_main.resolve_platform_plugin", return_value=None)
    def test_platform_plugin_raises_when_not_found(self, _mock):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            with self.assertRaises(DeployError):
                runner._platform_plugin()

    # 37
    @patch("media_stack.cli.commands.deploy_stack_main.resolve_platform_plugin")
    def test_platform_plugin_caches(self, mock_resolve):
        mock_resolve.return_value = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            p1 = runner._platform_plugin()
            p2 = runner._platform_plugin()
            self.assertIs(p1, p2)
            mock_resolve.assert_called_once()

    # 38
    def test_platform_client_cache_creates_once_and_rejects_empty_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            factory = MagicMock(return_value="client")
            r1 = runner.get_or_create_platform_client("docker", factory)
            r2 = runner.get_or_create_platform_client("docker", factory)
            self.assertIs(r1, r2)
            factory.assert_called_once()
            with self.assertRaises(DeployError):
                runner.get_or_create_platform_client("", lambda: None)

    # 39
    def test_write_runtime_artifact_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = _runner(_make_config(Path(tmp)))
            runner.runtime_artifacts_root = Path(tmp) / "artifacts"
            runner.runtime_artifacts_root.mkdir()
            result = runner._write_runtime_artifact_json(
                "shared", "data.json", {"key": "value"}, label="test"
            )
            self.assertIsNotNone(result)
            parsed = json.loads(result.read_text(encoding="utf-8"))
            self.assertEqual(parsed["key"], "value")

    # 40
    @patch("media_stack.cli.commands.deploy_stack_main.parse_deploy_stack_config")
    @patch("media_stack.cli.commands.deploy_stack_main.DeployStackRunner")
    def test_main_entry_point_success_and_failure(self, mock_runner_cls, mock_parse):
        # Success path
        mock_parse.return_value = MagicMock()
        mock_runner = MagicMock()
        mock_runner.run.return_value = 0
        mock_runner_cls.return_value = mock_runner
        self.assertEqual(main([]), 0)

        # Failure path
        mock_cfg = MagicMock(profile="full", namespace="ns")
        mock_parse.return_value = mock_cfg
        mock_runner_fail = MagicMock()
        mock_runner_fail.run.side_effect = DeployError("fail")
        mock_runner_fail.cfg = mock_cfg
        mock_runner_fail.tracker = MagicMock()
        mock_runner_cls.return_value = mock_runner_fail
        with patch("media_stack.cli.commands.deploy_stack_main.warn"):
            self.assertEqual(main([]), 1)


if __name__ == "__main__":
    unittest.main()
