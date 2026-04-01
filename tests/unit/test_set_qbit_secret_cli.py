import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT_PATH = (
    ROOT
    / "scripts"
    / "bootstrap_services"
    / "apps"
    / "qbittorrent"
    / "cli"
    / "set_qbit_secret_main.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("set_qbit_secret_main", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SetQbitSecretCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_parse_config_uses_env_defaults_when_args_missing(self):
        with mock.patch.dict(
            os.environ,
            {
                "NAMESPACE": "media-stack-dev",
                "STACK_ADMIN_USERNAME": "adminx",
                "STACK_ADMIN_PASSWORD": "passx",
            },
            clear=False,
        ):
            cfg = self.mod.parse_config([])
        self.assertEqual(cfg.namespace, "media-stack-dev")
        self.assertEqual(cfg.username, "adminx")
        self.assertEqual(cfg.password, "passx")

    def test_parse_config_rejects_partial_credentials(self):
        with self.assertRaises(self.mod.ConfigError):
            self.mod.parse_config(["only-user"])

    def test_run_patches_existing_secret_without_legacy_keys(self):
        cfg = self.mod.SetQbitSecretConfig(
            namespace="media-stack",
            username="admin",
            password="secret",
        )
        with mock.patch.object(self.mod, "resolve_kubectl_binary", return_value=["kubectl"]):
            with mock.patch.object(self.mod, "_secret_exists", return_value=True):
                with mock.patch.object(self.mod, "_patch_secret") as patch_secret:
                    rc = self.mod.run(cfg)
        self.assertEqual(rc, 0)
        patch_secret.assert_called_once()
        payload = patch_secret.call_args.args[2]
        self.assertEqual(
            payload,
            {"stringData": {"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "secret"}},
        )


if __name__ == "__main__":
    unittest.main()
