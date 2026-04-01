import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT_PATH = ROOT / "scripts" / "cli" / "ensure_qbit_credentials_main.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ensure_qbit_credentials_main", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class EnsureQbitCredentialsCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_parse_config_reads_env_defaults(self):
        with mock.patch.dict(
            os.environ,
            {
                "NAMESPACE": "media-stack-dev",
                "SECRET_NAME": "secret-x",
                "DEFAULT_STACK_ADMIN_USER": "u1",
                "DEFAULT_STACK_ADMIN_PASS": "p1",
            },
            clear=False,
        ):
            cfg = self.mod.parse_config([])

        self.assertEqual(cfg.namespace, "media-stack-dev")
        self.assertEqual(cfg.secret_name, "secret-x")
        self.assertEqual(cfg.default_stack_admin_user, "u1")
        self.assertEqual(cfg.default_stack_admin_pass, "p1")

    def test_resolve_target_credentials_prefers_stack_admin_when_enabled(self):
        cfg = self.mod.EnsureQbitCredentialsConfig(
            namespace="media-stack",
            secret_name="media-stack-secrets",
            default_stack_admin_user="admin",
            default_stack_admin_pass="stack-pass",
            rollout_timeout="5m",
            qbit_wait_seconds=120,
            qbit_deployment="qbittorrent",
            force_reset_on_auth_failure=True,
            qbit_force_config_sync=True,
            qbit_strict_login_check=False,
            qbit_api_validation=False,
        )

        resolved = self.mod.resolve_target_credentials(
            cfg,
            stack_admin_user="stack-user",
            stack_admin_pass="stack-pass",
        )

        self.assertEqual(resolved.qb_user, "stack-user")
        self.assertEqual(resolved.qb_pass, "stack-pass")

    def test_build_secret_patch_writes_stack_admin_only(self):
        creds = self.mod.CredentialResolution(
            stack_admin_user="admin",
            stack_admin_pass="stack-pass",
            qb_user="qb-user",
            qb_pass="qb-pass",
        )

        patch = self.mod.build_secret_patch(creds)
        self.assertNotIn("QBITTORRENT_USERNAME", patch["stringData"])
        self.assertNotIn("QBITTORRENT_PASSWORD", patch["stringData"])


if __name__ == "__main__":
    unittest.main()
