import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPT_PATH = ROOT / "scripts" / "cli" / "generate_secrets_main.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_secrets_main", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GenerateSecretsMainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_module()

    def test_build_secret_values_includes_full_canonical_keyset(self):
        values = self.mod.build_secret_values(
            current={"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "secret"},
            stack_admin_user="admin",
            pass_length=24,
            rotate_existing=False,
        )

        for key in self.mod.SECRET_KEY_DEFAULTS:
            self.assertIn(key, values)

        self.assertEqual(values["STACK_ADMIN_USERNAME"], "admin")
        self.assertEqual(values["STACK_ADMIN_PASSWORD"], "secret")
        self.assertEqual(values["JELLYSEERR_API_KEY"], "")
        self.assertEqual(values["TAUTULLI_API_KEY"], "")
        self.assertEqual(values["SONARR_API_KEY"], "")
        self.assertEqual(values["PROWLARR_API_KEY"], "")
        self.assertNotIn("UNPACKERR_SONARR_API_KEY", values)

    def test_build_secret_values_rotates_admin_password(self):
        with mock.patch.object(self.mod, "_rand_secret", return_value="new-pass"):
            values = self.mod.build_secret_values(
                current={"STACK_ADMIN_USERNAME": "old", "STACK_ADMIN_PASSWORD": "old-pass"},
                stack_admin_user="new-admin",
                pass_length=24,
                rotate_existing=True,
            )
        self.assertEqual(values["STACK_ADMIN_USERNAME"], "new-admin")
        self.assertEqual(values["STACK_ADMIN_PASSWORD"], "new-pass")

    def test_apply_secret_includes_extended_keys_in_manifest(self):
        observed: dict[str, str] = {}

        def _fake_run(cmd, *, check=True, input_text=None):
            observed["manifest"] = input_text or ""
            return mock.Mock(stdout="", returncode=0)

        values = self.mod.build_secret_values(
            current={"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": "secret"},
            stack_admin_user="admin",
            pass_length=24,
            rotate_existing=False,
        )
        with mock.patch.object(self.mod, "_run", side_effect=_fake_run):
            self.mod._apply_secret(["kubectl"], "media-stack", "media-stack-secrets", values)

        manifest = observed.get("manifest", "")
        self.assertIn("JELLYSEERR_API_KEY", manifest)
        self.assertIn("TAUTULLI_API_KEY", manifest)
        self.assertIn("SONARR_API_KEY", manifest)
        self.assertIn("PROWLARR_API_KEY", manifest)


if __name__ == "__main__":
    unittest.main()
