import os
import unittest
from unittest.mock import patch

from scripts.cli.jellyfin_bootstrap_config_service import parse_jellyfin_bootstrap_config


class JellyfinBootstrapConfigServiceTests(unittest.TestCase):
    def test_parse_config_from_env(self):
        env = {
            "NAMESPACE": "media-stack-dev",
            "SECRET_NAME": "secret-dev",
            "JELLYFIN_SERVICE_NAME": "jf",
            "JELLYFIN_BOOTSTRAP_WAIT_SECONDS": "240",
            "JELLYFIN_API_KEY_APP_NAME": "bootstrap-app",
            "STACK_ADMIN_USERNAME": "admin",
            "STACK_ADMIN_PASSWORD": "pw",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = parse_jellyfin_bootstrap_config([])
        self.assertEqual(cfg.namespace, "media-stack-dev")
        self.assertEqual(cfg.secret_name, "secret-dev")
        self.assertEqual(cfg.service_name, "jf")
        self.assertEqual(cfg.wait_seconds, 240)
        self.assertEqual(cfg.app_name, "bootstrap-app")
        self.assertEqual(cfg.stack_admin_username, "admin")
        self.assertEqual(cfg.stack_admin_password, "pw")


if __name__ == "__main__":
    unittest.main()
