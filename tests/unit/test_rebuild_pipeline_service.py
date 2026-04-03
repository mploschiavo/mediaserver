import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.deploy_pipeline_service import (  # noqa: E402
    DeployPipelineConfig,
    DeployPipelineService,
)


class DeployPipelineServiceTests(unittest.TestCase):
    def _svc(self, run_script):
        return DeployPipelineService(
            cfg=DeployPipelineConfig(
                namespace="media-stack",
                root_dir=ROOT,
                prepare_host_root="/srv/media-stack",
                enable_components="1",
                selected_apps="",
                internet_exposed="0",
                route_strategy="subdomain",
                ingress_domain="local",
                app_gateway_host="",
                app_gateway_port="",
                app_path_prefix="/app",
                media_server_direct_host="",
                preconfigure_api_keys="1",
                apply_initial_preferences="1",
                auto_download_content="0",
                config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
                auth_provider="authelia",
                auth_middleware="authelia@docker",
                edge_router_provider="traefik",
            ),
            info=mock.Mock(),
            run_script=run_script,
        )

    def test_prepare_host_directories_skips_non_legacy(self):
        svc = self._svc(mock.Mock())
        self.assertFalse(svc.prepare_host_directories("dynamic-pvc"))

    def test_generate_secrets_runs_script(self):
        run_script = mock.Mock()
        svc = self._svc(run_script)
        svc.generate_secrets()
        run_script.assert_called_once()

    def test_run_bootstrap_pipeline_passes_profile_flags(self):
        run_script = mock.Mock()
        svc = self._svc(run_script)
        svc.run_bootstrap_pipeline()
        run_script.assert_called_once()
        _script_name, _config_path = run_script.call_args.args
        self.assertEqual(_script_name, "bootstrap-all.sh")
        env = dict(run_script.call_args.kwargs.get("env") or {})
        self.assertEqual(env.get("PRECONFIGURE_API_KEYS"), "1")
        self.assertEqual(env.get("APPLY_INITIAL_PREFERENCES"), "1")
        self.assertEqual(env.get("AUTO_DOWNLOAD_CONTENT"), "0")
        self.assertEqual(env.get("AUTH_PROVIDER"), "authelia")
        self.assertEqual(env.get("AUTH_MIDDLEWARE"), "authelia@docker")
        self.assertEqual(env.get("EDGE_ROUTER_PROVIDER"), "traefik")
        self.assertIsNone(env.get("APP_GATEWAY_PORT"))

    def test_run_bootstrap_pipeline_passes_gateway_port_when_configured(self):
        run_script = mock.Mock()
        svc = self._svc(run_script)
        svc.cfg = DeployPipelineConfig(
            **{**svc.cfg.__dict__, "app_gateway_port": "18080"}  # type: ignore[arg-type]
        )
        svc.run_bootstrap_pipeline()
        env = dict(run_script.call_args.kwargs.get("env") or {})
        self.assertEqual(env.get("APP_GATEWAY_PORT"), "18080")
        self.assertEqual(env.get("TRAEFIK_HTTP_PORT"), "18080")


if __name__ == "__main__":
    unittest.main()
