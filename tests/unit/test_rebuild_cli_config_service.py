import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.cli.rebuild_cli_config_service import parse_rebuild_bootstrap_config


class RebuildCliConfigServiceTests(unittest.TestCase):
    def test_parse_rebuild_bootstrap_config(self):
        root_dir = Path("/tmp/media-stack-test")
        env = {
            "PROFILE": "full",
            "RUN_BOOTSTRAP": "1",
            "PRECONFIGURE_API_KEYS": "0",
            "APPLY_INITIAL_PREFERENCES": "0",
            "AUTO_DOWNLOAD_CONTENT": "1",
            "STORAGE_MODE": "dynamic-pvc",
            "CONFIG_FILE": "/tmp/media-stack-test/bootstrap/custom.json",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = parse_rebuild_bootstrap_config(
                [
                    "192.168.1.60",
                    "--platform-target",
                    "kubernetes",
                    "--namespace",
                    "media-stack-dev",
                    "--ingress-domain",
                    "local",
                    "--compose-project-name",
                    "media-dev",
                ],
                root_dir=root_dir,
            )
        self.assertEqual(cfg.platform_target, "kubernetes")
        self.assertEqual(cfg.namespace, "media-stack-dev")
        self.assertEqual(cfg.node_ip, "192.168.1.60")
        self.assertEqual(cfg.profile, "full")
        self.assertEqual(cfg.run_bootstrap, "1")
        self.assertEqual(cfg.preconfigure_api_keys, "0")
        self.assertEqual(cfg.apply_initial_preferences, "0")
        self.assertEqual(cfg.auto_download_content, "1")
        self.assertEqual(cfg.config_file, Path("/tmp/media-stack-test/bootstrap/custom.json"))
        self.assertEqual(cfg.compose_project_name, "media-dev")
        self.assertEqual(cfg.compose_file, root_dir / "docker" / "docker-compose.yml")
        self.assertEqual(cfg.compose_env_file, root_dir / "docker" / ".env")

    def test_parse_rebuild_bootstrap_config_uses_bootstrap_profile_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            bootstrap_dir = root_dir / "bootstrap"
            bootstrap_dir.mkdir(parents=True, exist_ok=True)
            (bootstrap_dir / "media-stack.bootstrap.yaml").write_text(
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
                        "  base_domain: example.com",
                        "auth:",
                        "  enabled: true",
                        "  provider: authentik",
                    ]
                ),
                encoding="utf-8",
            )
            cfg = parse_rebuild_bootstrap_config([], root_dir=root_dir)
        self.assertEqual(cfg.platform_target, "compose")
        self.assertEqual(cfg.namespace, "media-prod")
        self.assertEqual(cfg.compose_project_name, "media-prod")
        self.assertEqual(cfg.profile, "power-user")
        self.assertEqual(cfg.run_bootstrap, "1")
        self.assertEqual(cfg.preconfigure_api_keys, "1")
        self.assertEqual(cfg.apply_initial_preferences, "1")
        self.assertEqual(cfg.auto_download_content, "0")
        self.assertEqual(cfg.internet_exposed, "1")
        self.assertEqual(cfg.route_strategy, "path-prefix")
        self.assertEqual(cfg.auth_provider, "authentik")
        self.assertEqual(cfg.auth_middleware, "authentik@docker")
        self.assertEqual(cfg.ingress_domain, "media-prod.example.com")
        self.assertEqual(cfg.disk_allocation_gb, 2000)
        self.assertEqual(cfg.network_cidr, "10.44.0.0/24")
        self.assertIn("jellyfin", cfg.selected_apps)
        self.assertNotIn("sabnzbd", cfg.selected_apps)


if __name__ == "__main__":
    unittest.main()
