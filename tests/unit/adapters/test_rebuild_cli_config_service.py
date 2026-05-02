import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.deploy_cli_config_service import parse_deploy_stack_config


class RebuildCliConfigServiceTests(unittest.TestCase):
    def test_parse_deploy_stack_config(self):
        root_dir = Path("/tmp/media-stack-test")
        env = {
            "PLATFORM_TARGET": "kubernetes",
            "NAMESPACE": "media-stack-dev",
            "INGRESS_DOMAIN": "local",
            "COMPOSE_PROJECT_NAME": "media-dev",
            "PROFILE": "full",
            "RUN_BOOTSTRAP": "1",
            "PRECONFIGURE_API_KEYS": "0",
            "APPLY_INITIAL_PREFERENCES": "0",
            "AUTO_DOWNLOAD_CONTENT": "1",
            "STORAGE_MODE": "dynamic-pvc",
            "CONFIG_FILE": "/tmp/media-stack-test/contracts/custom.json",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = parse_deploy_stack_config(
                ["192.168.1.60"],
                root_dir=root_dir,
            )
        self.assertEqual(cfg.platform_target, "k8s")
        self.assertEqual(cfg.namespace, "media-stack-dev")
        self.assertEqual(cfg.node_ip, "192.168.1.60")
        self.assertEqual(cfg.profile, "full")
        self.assertEqual(cfg.run_bootstrap, "1")
        self.assertEqual(cfg.preconfigure_api_keys, "0")
        self.assertEqual(cfg.apply_initial_preferences, "0")
        self.assertEqual(cfg.auto_download_content, "1")
        self.assertEqual(cfg.config_file, Path("/tmp/media-stack-test/contracts/custom.json"))
        self.assertEqual(cfg.compose_project_name, "media-dev")
        self.assertEqual(cfg.compose_file, root_dir / "deploy" / "compose" / "docker-compose.yml")
        self.assertEqual(cfg.compose_env_file, root_dir / "docker" / ".env")

    def test_delete_namespace_defaults_to_zero_when_env_unset(self):
        """Teardown must be explicitly opted-in via DELETE_NAMESPACE=1; never the silent default."""
        root_dir = Path("/tmp/media-stack-test")
        env = {
            "PLATFORM_TARGET": "kubernetes",
            "NAMESPACE": "media-stack-dev",
            "INGRESS_DOMAIN": "local",
        }
        with patch.dict(os.environ, env, clear=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DELETE_NAMESPACE", None)
                os.environ.pop("DELETE_NAMESPACE_CONFIRM", None)
                cfg = parse_deploy_stack_config([], root_dir=root_dir)
        self.assertEqual(cfg.delete_namespace, "0")
        self.assertEqual(cfg.delete_namespace_confirm, "")

    def test_delete_namespace_respected_when_set_to_one(self):
        root_dir = Path("/tmp/media-stack-test")
        env = {
            "PLATFORM_TARGET": "kubernetes",
            "NAMESPACE": "media-stack-dev",
            "INGRESS_DOMAIN": "local",
            "DELETE_NAMESPACE": "1",
            "DELETE_NAMESPACE_CONFIRM": "media-stack-dev",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = parse_deploy_stack_config([], root_dir=root_dir)
        self.assertEqual(cfg.delete_namespace, "1")
        self.assertEqual(cfg.delete_namespace_confirm, "media-stack-dev")

    def test_parse_deploy_stack_config_uses_bootstrap_profile_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            bootstrap_dir = root_dir / "contracts"
            bootstrap_dir.mkdir(parents=True, exist_ok=True)
            (bootstrap_dir / "media-stack.profile.yaml").write_text(
                "\n".join(
                    [
                        "schema_version: 1",
                        "kind: media_stack_profile",
                        "metadata:",
                        "  name: media-prod",
                        "  platform: compose",
                        "  purpose: prod",
                        "resources:",
                        "  disk_space_gb: 2TB",
                        "  network_cidr: 10.44.0.0/24",
                        "install_profile: standard",
                        "apps:",
                        "  sabnzbd: false",
                        "bootstrap:",
                        "  preconfigure_apps: true",
                        "  preconfigure_api_keys: true",
                        "  apply_initial_preferences: true",
                        "  auto_download_content: false",
                        "routing:",
                        "  internet_exposed: true",
                        "  strategy: path-prefix",
                        "  provider: envoy",
                        "  base_domain: example.com",
                        "  gateway_port: 18080",
                        "auth:",
                        "  enabled: true",
                        "  provider: authentik",
                        "chaos:",
                        "  enabled: true",
                        "  duration_minutes: 5",
                        "  interval_seconds: 30",
                        "  actions: [restart_container, pause_container, network_disconnect]",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = parse_deploy_stack_config([], root_dir=root_dir)
        self.assertEqual(cfg.platform_target, "compose")
        self.assertEqual(cfg.namespace, "media-prod")
        self.assertEqual(cfg.compose_project_name, "media-prod")
        self.assertEqual(cfg.profile, "standard")
        self.assertEqual(cfg.run_bootstrap, "1")
        self.assertEqual(cfg.preconfigure_api_keys, "1")
        self.assertEqual(cfg.apply_initial_preferences, "1")
        self.assertEqual(cfg.auto_download_content, "0")
        self.assertEqual(cfg.internet_exposed, "1")
        self.assertEqual(cfg.route_strategy, "path-prefix")
        self.assertEqual(cfg.auth_provider, "authentik")
        self.assertEqual(cfg.auth_middleware, "authentik@docker")
        self.assertEqual(cfg.edge_router_provider, "envoy")
        self.assertEqual(cfg.app_gateway_port, "18080")
        self.assertEqual(cfg.ingress_domain, "media-prod.example.com")
        self.assertEqual(cfg.disk_allocation_gb, 2000)
        self.assertEqual(cfg.network_cidr, "10.44.0.0/24")
        self.assertEqual(cfg.chaos_enabled, "1")
        self.assertEqual(cfg.chaos_duration_minutes, 5)
        self.assertEqual(cfg.chaos_interval_seconds, 30)
        self.assertEqual(
            cfg.chaos_actions,
            "restart_container,pause_container,network_disconnect",
        )
        self.assertIn("jellyfin", cfg.selected_apps)
        self.assertNotIn("sabnzbd", cfg.selected_apps)

    def test_parse_deploy_stack_config_respects_preconfigure_apps_flag_for_run_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            bootstrap_dir = root_dir / "contracts"
            bootstrap_dir.mkdir(parents=True, exist_ok=True)
            (bootstrap_dir / "media-stack.profile.yaml").write_text(
                "\n".join(
                    [
                        "schema_version: 1",
                        "kind: media_stack_profile",
                        "metadata:",
                        "  name: media-dev",
                        "  platform: compose",
                        "  purpose: dev",
                        "resources:",
                        "  disk_space_gb: 50",
                        "  network_cidr: 192.168.1.0/24",
                        "install_profile: standard",
                        "bootstrap:",
                        "  preconfigure_apps: false",
                        "  preconfigure_api_keys: true",
                        "  apply_initial_preferences: true",
                        "  auto_download_content: false",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = parse_deploy_stack_config([], root_dir=root_dir)
        self.assertEqual(cfg.run_bootstrap, "0")


if __name__ == "__main__":
    unittest.main()
