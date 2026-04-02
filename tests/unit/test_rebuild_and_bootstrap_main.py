import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_and_bootstrap_main import main  # noqa: E402
from cli.rebuild_cli_config_service import RebuildBootstrapConfig  # noqa: E402


class RebuildAndBootstrapMainTests(unittest.TestCase):
    def test_main_skips_kubernetes_client_resolution_for_compose_target(self):
        cfg = RebuildBootstrapConfig(root_dir=Path("/tmp"), platform_target="compose")
        with (
            mock.patch(
                "cli.rebuild_and_bootstrap_main.parse_rebuild_bootstrap_config",
                return_value=cfg,
            ),
            mock.patch(
                "cli.rebuild_and_bootstrap_main.KubernetesClient.from_environment",
                side_effect=AssertionError(
                    "KubernetesClient.from_environment should not be called"
                ),
            ),
            mock.patch("cli.rebuild_and_bootstrap_main.RebuildBootstrapRunner") as runner_cls,
        ):
            runner_cls.return_value.run.return_value = 0
            rc = main([])
        self.assertEqual(rc, 0)
        runner_cls.assert_called_once_with(cfg=cfg, kube=None)

    def test_main_resolves_kubernetes_client_for_k8s_target(self):
        cfg = RebuildBootstrapConfig(root_dir=Path("/tmp"), platform_target="k8s")
        kube = mock.Mock()
        with (
            mock.patch(
                "cli.rebuild_and_bootstrap_main.parse_rebuild_bootstrap_config",
                return_value=cfg,
            ),
            mock.patch(
                "cli.rebuild_and_bootstrap_main.KubernetesClient.from_environment",
                return_value=kube,
            ) as from_environment,
            mock.patch("cli.rebuild_and_bootstrap_main.RebuildBootstrapRunner") as runner_cls,
        ):
            runner_cls.return_value.run.return_value = 0
            rc = main([])
        self.assertEqual(rc, 0)
        from_environment.assert_called_once()
        runner_cls.assert_called_once_with(cfg=cfg, kube=kube)


if __name__ == "__main__":
    unittest.main()
