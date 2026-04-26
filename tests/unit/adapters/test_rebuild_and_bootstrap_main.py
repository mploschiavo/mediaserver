import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.commands.deploy_stack_main import main  # noqa: E402
from media_stack.cli.workflows.deploy_cli_config_service import DeployStackConfig  # noqa: E402


class DeployStackMainTests(unittest.TestCase):
    def test_main_builds_runner_without_target_specific_client_resolution_for_compose_target(self):
        cfg = DeployStackConfig(root_dir=Path("/tmp"), platform_target="compose")
        with (
            mock.patch(
                "media_stack.cli.commands.deploy_stack_main.parse_deploy_stack_config",
                return_value=cfg,
            ),
            mock.patch("media_stack.cli.commands.deploy_stack_main.DeployStackRunner") as runner_cls,
        ):
            runner_cls.return_value.run.return_value = 0
            rc = main([])
        self.assertEqual(rc, 0)
        runner_cls.assert_called_once_with(cfg=cfg)

    def test_main_builds_runner_without_target_specific_client_resolution_for_k8s_target(self):
        cfg = DeployStackConfig(root_dir=Path("/tmp"), platform_target="k8s")
        with (
            mock.patch(
                "media_stack.cli.commands.deploy_stack_main.parse_deploy_stack_config",
                return_value=cfg,
            ),
            mock.patch("media_stack.cli.commands.deploy_stack_main.DeployStackRunner") as runner_cls,
        ):
            runner_cls.return_value.run.return_value = 0
            rc = main([])
        self.assertEqual(rc, 0)
        runner_cls.assert_called_once_with(cfg=cfg)


if __name__ == "__main__":
    unittest.main()
