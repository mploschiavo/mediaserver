import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.deploy_cli_config_service import DeployStackConfig  # noqa: E402
from cli.deploy_stack_main import DeployError, DeployStackRunner  # noqa: E402


class DeployStackEdgeProviderValidationTests(unittest.TestCase):
    def _cfg(
        self,
        root_dir: Path,
        *,
        router_provider: str,
        compose_provider_specs: dict[str, dict[str, str]] | None = None,
    ) -> DeployStackConfig:
        config_file = root_dir / "bootstrap-config.json"
        payload = {
            "adapter_hooks": {
                "edge": {
                    "router_provider": router_provider,
                    "compose_provider_specs": compose_provider_specs or {},
                }
            }
        }
        config_file.write_text(json.dumps(payload), encoding="utf-8")
        return DeployStackConfig(
            root_dir=root_dir,
            platform_target="compose",
            config_file=config_file,
            auth_provider="none",
            run_bootstrap="0",
        )

    def test_validate_inputs_allows_builtin_envoy_stub_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            runner = DeployStackRunner(
                cfg=self._cfg(root_dir, router_provider="envoy"),
            )
            runner._validate_inputs()

    def test_explicit_config_provider_overrides_bootstrap_hook_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            cfg = self._cfg(root_dir, router_provider="traefik")
            cfg.edge_router_provider = "envoy"
            runner = DeployStackRunner(cfg=cfg)
            self.assertEqual(runner._edge_router_provider(), "envoy")
            runner._validate_inputs()

    def test_validate_inputs_rejects_non_builtin_provider_without_compose_bindings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            runner = DeployStackRunner(
                cfg=self._cfg(
                    root_dir,
                    router_provider="custom-edge",
                    compose_provider_specs={"custom-edge": {}},
                ),
            )
            with self.assertRaises(DeployError) as ctx:
                runner._validate_inputs()
            self.assertIn("Compose edge provider bindings are missing", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
