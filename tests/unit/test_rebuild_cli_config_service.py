import os
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
                ],
                root_dir=root_dir,
            )
        self.assertEqual(cfg.platform_target, "kubernetes")
        self.assertEqual(cfg.namespace, "media-stack-dev")
        self.assertEqual(cfg.node_ip, "192.168.1.60")
        self.assertEqual(cfg.profile, "full")
        self.assertEqual(cfg.run_bootstrap, "1")
        self.assertEqual(cfg.config_file, Path("/tmp/media-stack-test/bootstrap/custom.json"))


if __name__ == "__main__":
    unittest.main()
