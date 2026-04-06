import os
import unittest
from pathlib import Path
from unittest.mock import patch

from media_stack.cli.workflows.run_bootstrap_job_cli_config_service import parse_run_bootstrap_job_config


class RunBootstrapJobCliConfigServiceTests(unittest.TestCase):
    def test_parse_config_and_timeout_seconds(self):
        root_dir = Path("/tmp/media-stack")
        env = {
            "NAMESPACE": "media-stack-dev",
            "TIMEOUT": "90s",
            "HEARTBEAT_INTERVAL": "20",
            "JOB_LOG_TAIL_LINES": "200",
            "BOOTSTRAP_RUNNER_IMAGE": "registry.local/bootstrap:dev",
            "PRECONFIGURE_API_KEYS": "1",
            "APPLY_INITIAL_PREFERENCES": "1",
            "AUTO_DOWNLOAD_CONTENT": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = parse_run_bootstrap_job_config([], root_dir=root_dir)

        self.assertEqual(cfg.namespace, "media-stack-dev")
        self.assertEqual(cfg.timeout_seconds, 90)
        self.assertEqual(cfg.heartbeat_interval, 20)
        self.assertEqual(cfg.job_log_tail_lines, 200)
        self.assertEqual(cfg.bootstrap_runner_image, "registry.local/bootstrap:dev")
        self.assertTrue(cfg.preconfigure_api_keys)
        self.assertTrue(cfg.apply_initial_preferences)
        self.assertFalse(cfg.auto_download_content)

    def test_parse_dynamic_skip_flags_from_env(self):
        root_dir = Path("/tmp/media-stack")
        with patch.dict(
            os.environ,
            {
                "SKIP_TORRENT_CLIENT_ENSURE": "1",
                "SKIP_USENET_CLIENT_ENSURE": "1",
            },
            clear=False,
        ):
            cfg = parse_run_bootstrap_job_config([], root_dir=root_dir)

        self.assertTrue(cfg.effective_phase_skip_flags.get("skip_torrent_client_ensure"))
        self.assertTrue(cfg.effective_phase_skip_flags.get("skip_usenet_client_ensure"))


if __name__ == "__main__":
    unittest.main()
