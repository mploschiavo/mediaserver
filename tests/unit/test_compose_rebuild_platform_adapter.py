import tempfile
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.compose.rebuild_platform_adapter import (  # noqa: E402
    ComposeRebuildPlatformAdapter,
    ComposeRebuildPlatformConfig,
)
from core.platforms.compose.docker_client import DockerContainerState  # noqa: E402

_TRAEFIK_EDGE_SPEC = {
    "enable_label_key": "traefik.enable",
    "router_label_prefix": "traefik.http.routers.",
    "router_rule_key_template": "traefik.http.routers.{router_name}.rule",
    "router_service_key_template": "traefik.http.routers.{router_name}.service",
    "router_middleware_key_template": "traefik.http.routers.{router_name}.middlewares",
    "strip_prefix_key_template": "traefik.http.middlewares.{middleware_name}.stripprefix.prefixes",
    "path_rule_template": "Host(`{gateway_host}`) && PathPrefix(`{path_prefix}`)",
    "media_server_rule_key_template": "traefik.http.routers.{service_name}.rule",
    "direct_host_rule_template": "Host(`{direct_host}`)",
}

_AUTH_MIDDLEWARE_DEFAULTS = {
    "none": "",
    "authelia": "authelia@docker",
    "authentik": "authentik@docker",
}


def _compose_text() -> str:
    return (
        "services:\n"
        "  app:\n"
        "    image: ghcr.io/example/app:latest\n"
        "    container_name: app\n"
        "    healthcheck:\n"
        '      test: ["CMD", "true"]\n'
        "      interval: 1s\n"
        "  optional:\n"
        "    image: ghcr.io/example/optional:latest\n"
        "    container_name: optional\n"
        '    profiles: ["optional"]\n'
    )


def _compose_text_with_edge_labels() -> str:
    return (
        "services:\n"
        "  traefik:\n"
        "    image: ghcr.io/example/traefik:latest\n"
        "    container_name: traefik\n"
        "  jellyfin:\n"
        "    image: ghcr.io/example/jellyfin:latest\n"
        "    container_name: jellyfin\n"
        "    labels:\n"
        "      - traefik.enable=true\n"
        "      - traefik.http.routers.jellyfin.rule=Host(`jellyfin.old.local`)\n"
        "      - traefik.http.services.jellyfin.loadbalancer.server.port=8096\n"
        "  sonarr:\n"
        "    image: ghcr.io/example/sonarr:latest\n"
        "    container_name: sonarr\n"
        "    labels:\n"
        "      - traefik.enable=true\n"
        "      - traefik.http.routers.sonarr.rule=Host(`sonarr.old.local`)\n"
        "      - traefik.http.services.sonarr.loadbalancer.server.port=8989\n"
    )


def _compose_text_with_user_bind_mount(mount_path: Path) -> str:
    return (
        "services:\n"
        "  app:\n"
        "    image: ghcr.io/example/app:latest\n"
        "    container_name: app\n"
        '    user: "1000:1000"\n'
        f"    volumes:\n      - {mount_path}:/config\n"
    )


def _compose_text_with_unpackerr_placeholder_key() -> str:
    return (
        "services:\n"
        "  unpackerr:\n"
        "    image: ghcr.io/example/unpackerr:latest\n"
        "    container_name: unpackerr\n"
        "    environment:\n"
        "      UN_LIDARR_0_API_KEY: replace-after-first-boot\n"
    )


class ComposeRebuildPlatformAdapterTests(unittest.TestCase):
    def _adapter(
        self,
        *,
        compose_file: Path,
        compose_env_file: Path | None = None,
        docker=None,
        compose_profiles: tuple[str, ...] = (),
        selected_apps: tuple[str, ...] = (),
        node_ip: str = "",
        route_strategy: str = "subdomain",
        app_gateway_host: str = "",
        media_server_direct_host: str = "",
        internet_exposed: bool = False,
        auth_provider: str = "none",
        auth_middleware: str = "",
        edge_router_provider: str = "traefik",
        edge_router_service_names: tuple[str, ...] = ("traefik",),
        edge_compose_provider_specs: dict[str, dict[str, str]] | None = None,
        runtime_artifacts_dir: Path | None = None,
    ) -> ComposeRebuildPlatformAdapter:
        docker_client = docker or mock.Mock()
        if isinstance(getattr(docker_client, "list_running_containers", None), mock.Mock):
            docker_client.list_running_containers.return_value = []
        return ComposeRebuildPlatformAdapter(
            cfg=ComposeRebuildPlatformConfig(
                environment_id="media-dev",
                compose_file=compose_file,
                compose_env_file=compose_env_file,
                compose_profiles=compose_profiles,
                selected_apps=selected_apps,
                node_ip=node_ip,
                route_strategy=route_strategy,
                allowed_route_strategies=("subdomain", "path-prefix", "hybrid"),
                app_gateway_host=app_gateway_host,
                media_server_direct_host=media_server_direct_host,
                internet_exposed=internet_exposed,
                auth_provider=auth_provider,
                auth_middleware=auth_middleware,
                edge_router_provider=edge_router_provider,
                edge_router_service_names=edge_router_service_names,
                edge_compose_provider_specs=dict(
                    edge_compose_provider_specs or {"traefik": dict(_TRAEFIK_EDGE_SPEC)}
                ),
                auth_provider_middleware_defaults=dict(_AUTH_MIDDLEWARE_DEFAULTS),
                media_server_service_names=("jellyfin", "jellyfin-nvidia"),
                runtime_artifacts_dir=runtime_artifacts_dir,
            ),
            info=mock.Mock(),
            docker=docker_client,
        )

    def test_environment_ref_uses_environment_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            adapter = self._adapter(compose_file=compose_file)
        self.assertEqual(adapter.environment.environment_id, "media-dev")
        self.assertEqual(adapter.environment.target, "compose")

    def test_delete_environment_optional_is_skipped_when_not_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            docker = mock.Mock()
            adapter = self._adapter(compose_file=compose_file, docker=docker)
            self.assertFalse(adapter.delete_environment_optional("0"))
            docker.ping.assert_not_called()

    def test_apply_environment_definition_deploys_selected_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(compose_file=compose_file, docker=docker)

            adapter.apply_environment_definition()

            docker.ping.assert_called_once()
            docker.ensure_network.assert_called_once()
            docker.pull_image.assert_called_once_with("ghcr.io/example/app:latest")
            docker.remove_container.assert_called_once_with("app", force=True)
            docker.create_container.assert_called_once()
            container.start.assert_called_once()

    def test_apply_environment_definition_honors_compose_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(
                compose_file=compose_file,
                docker=docker,
                compose_profiles=("optional",),
            )

            adapter.apply_environment_definition()

            self.assertEqual(docker.pull_image.call_count, 2)
            docker.remove_container.assert_any_call("app", force=True)
            docker.remove_container.assert_any_call("optional", force=True)

    def test_apply_environment_definition_honors_selected_apps_without_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(
                compose_file=compose_file,
                docker=docker,
                selected_apps=("optional",),
            )

            adapter.apply_environment_definition()

            docker.pull_image.assert_called_once_with("ghcr.io/example/optional:latest")
            docker.remove_container.assert_called_once_with("optional", force=True)

    def test_apply_environment_definition_adds_path_prefix_and_auth_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text_with_edge_labels(), encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(
                compose_file=compose_file,
                docker=docker,
                route_strategy="path-prefix",
                app_gateway_host="apps.media-dev.example.com",
                media_server_direct_host="jellyfin.media-dev.example.com",
                internet_exposed=True,
                auth_provider="authelia",
            )

            adapter.apply_environment_definition()

            call_kwargs = [call.kwargs for call in docker.create_container.call_args_list]
            sonarr_labels = {}
            jellyfin_labels = {}
            for kwargs in call_kwargs:
                if kwargs.get("name") == "sonarr":
                    sonarr_labels = dict(kwargs.get("labels") or {})
                if kwargs.get("name") == "jellyfin":
                    jellyfin_labels = dict(kwargs.get("labels") or {})

            self.assertEqual(
                sonarr_labels.get("traefik.http.routers.sonarr-path.rule"),
                "Host(`apps.media-dev.example.com`) && PathPrefix(`/app/sonarr`)",
            )
            self.assertIn(
                "authelia@docker",
                sonarr_labels.get("traefik.http.routers.sonarr-path.middlewares", ""),
            )
            self.assertEqual(
                jellyfin_labels.get("traefik.http.routers.jellyfin.rule"),
                "Host(`jellyfin.media-dev.example.com`)",
            )
            self.assertNotIn(
                "authelia@docker",
                jellyfin_labels.get("traefik.http.routers.jellyfin.middlewares", ""),
            )

    def test_wait_for_workloads_succeeds_when_running_and_healthy(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            docker = mock.Mock()
            docker.container_state.return_value = DockerContainerState(
                name="app",
                status="running",
                health="healthy",
                image="ghcr.io/example/app:latest",
            )
            adapter = self._adapter(compose_file=compose_file, docker=docker)

            adapter.wait_for_workloads()

    def test_run_smoke_test_returns_configured_node_ip(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            docker = mock.Mock()
            docker.container_state.return_value = DockerContainerState(
                name="app",
                status="running",
                health="healthy",
                image="ghcr.io/example/app:latest",
            )
            adapter = self._adapter(
                compose_file=compose_file,
                docker=docker,
                node_ip="192.168.1.10",
            )
            self.assertEqual(adapter.run_smoke_test(), "192.168.1.10")

    def test_print_workload_status_emits_service_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            docker = mock.Mock()
            docker.container_state.return_value = DockerContainerState(
                name="app",
                status="running",
                health="healthy",
                image="ghcr.io/example/app:latest",
            )
            adapter = self._adapter(compose_file=compose_file, docker=docker)

            adapter.print_workload_status()

            info_messages = [call.args[0] for call in adapter.info.call_args_list if call.args]
            self.assertTrue(any("compose/app" in message for message in info_messages))

    def test_secret_lifecycle_methods_are_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            adapter = self._adapter(compose_file=compose_file, docker=mock.Mock())

            self.assertEqual(adapter.backup_secret_values("1"), {})
            self.assertIsNone(adapter.restore_secret_values({"STACK_ADMIN_PASSWORD": "secret"}))

    def test_apply_environment_definition_writes_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            artifacts_dir = Path(tmp) / "runtime-artifacts"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(
                compose_file=compose_file,
                docker=docker,
                runtime_artifacts_dir=artifacts_dir,
            )

            adapter.apply_environment_definition()

            expanded = artifacts_dir / "resolved" / "docker-compose.expanded.yaml"
            selected = artifacts_dir / "resolved" / "docker-compose.selected.runtime.yaml"
            deploy_plan = artifacts_dir / "resolved" / "deploy-plan.json"
            self.assertTrue(expanded.exists())
            self.assertTrue(selected.exists())
            self.assertTrue(deploy_plan.exists())
            self.assertIn("services:", expanded.read_text(encoding="utf-8"))
            self.assertIn("ghcr.io/example/app:latest", selected.read_text(encoding="utf-8"))

    def test_apply_environment_definition_writes_traefik_dynamic_file_provider_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text_with_edge_labels(), encoding="utf-8")
            compose_env_file = Path(tmp) / ".env"
            config_root = Path(tmp) / "config"
            compose_env_file.write_text(f"CONFIG_ROOT={config_root}\n", encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(
                compose_file=compose_file,
                compose_env_file=compose_env_file,
                docker=docker,
            )

            adapter.apply_environment_definition()

            dynamic_path = config_root / "traefik" / "dynamic" / "media-stack.dynamic.yaml"
            self.assertTrue(dynamic_path.exists())
            rendered = yaml.safe_load(dynamic_path.read_text(encoding="utf-8")) or {}
            http_cfg = rendered.get("http") or {}
            routers = http_cfg.get("routers") or {}
            services = http_cfg.get("services") or {}
            self.assertEqual(
                (routers.get("sonarr") or {}).get("rule"),
                "Host(`sonarr.old.local`)",
            )
            self.assertEqual(
                (
                    (
                        ((services.get("sonarr") or {}).get("loadBalancer") or {}).get("servers")
                        or [{}]
                    )[0]
                ).get("url"),
                "http://sonarr:8989",
            )

    def test_apply_environment_definition_envoy_stub_skips_traefik_patch_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text_with_edge_labels(), encoding="utf-8")
            compose_env_file = Path(tmp) / ".env"
            config_root = Path(tmp) / "config"
            compose_env_file.write_text(f"CONFIG_ROOT={config_root}\n", encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(
                compose_file=compose_file,
                compose_env_file=compose_env_file,
                docker=docker,
                edge_router_provider="envoy",
                edge_router_service_names=("envoy",),
                edge_compose_provider_specs={"envoy": {}},
            )

            adapter.apply_environment_definition()

            dynamic_path = config_root / "traefik" / "dynamic" / "media-stack.dynamic.yaml"
            self.assertFalse(dynamic_path.exists())
            info_messages = [call.args[0] for call in adapter.info.call_args_list if call.args]
            self.assertTrue(
                any(
                    "envoy" in message and "stub/no-op compose label bindings" in message
                    for message in info_messages
                )
            )

    def test_apply_environment_definition_precreates_missing_rw_bind_mount_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            mount_path = Path(tmp) / "config" / "app"
            compose_file.write_text(
                _compose_text_with_user_bind_mount(mount_path),
                encoding="utf-8",
            )
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(compose_file=compose_file, docker=docker)

            adapter.apply_environment_definition()

            self.assertTrue(mount_path.exists())
            self.assertTrue(mount_path.is_dir())
            docker.create_container.assert_called_once()

    def test_apply_environment_definition_fails_fast_for_unwritable_user_bind_mount_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            mount_path = Path(tmp) / "config" / "app"
            mount_path.mkdir(parents=True, exist_ok=True)
            mount_path.chmod(0o555)
            compose_file.write_text(
                _compose_text_with_user_bind_mount(mount_path),
                encoding="utf-8",
            )
            docker = mock.Mock()
            adapter = self._adapter(compose_file=compose_file, docker=docker)

            with self.assertRaisesRegex(RuntimeError, "not writable"):
                adapter.apply_environment_definition()
            docker.create_container.assert_not_called()

    def test_apply_environment_definition_normalizes_unpackerr_placeholder_api_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(
                _compose_text_with_unpackerr_placeholder_key(),
                encoding="utf-8",
            )
            container = mock.Mock()
            docker = mock.Mock()
            docker.create_container.return_value = container
            adapter = self._adapter(compose_file=compose_file, docker=docker)

            adapter.apply_environment_definition()

            create_kwargs = docker.create_container.call_args.kwargs
            self.assertEqual(
                (create_kwargs.get("environment") or {}).get("UN_LIDARR_0_API_KEY"),
                "00000000000000000000000000000000",
            )

    @mock.patch("core.platforms.compose.rebuild_platform_adapter.time.sleep", return_value=None)
    def test_run_chaos_tests_executes_declared_actions(self, _sleep_mock):
        with tempfile.TemporaryDirectory() as tmp:
            compose_file = Path(tmp) / "docker-compose.yml"
            compose_file.write_text(_compose_text(), encoding="utf-8")
            container = mock.Mock()
            docker = mock.Mock()
            docker.get_container.return_value = container
            docker.container_state.return_value = DockerContainerState(
                name="app",
                status="running",
                health="healthy",
                image="ghcr.io/example/app:latest",
            )
            network = mock.Mock()
            docker.client.networks.get.return_value = network
            adapter = self._adapter(compose_file=compose_file, docker=docker)

            adapter.run_chaos_tests(
                duration_minutes=5,
                interval_seconds=0,
                actions=("restart_container", "pause_container", "network_disconnect"),
            )

            container.restart.assert_called_once()
            container.pause.assert_called_once()
            container.unpause.assert_called_once()
            network.disconnect.assert_called_once()
            network.connect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
