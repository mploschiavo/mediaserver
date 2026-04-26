import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
SCRIPT_PATH = ROOT / "src" / "media_stack" / "cli" / "commands" / "generate_secrets_main.py"
if not SCRIPT_PATH.exists():
    SCRIPT_PATH = (
        ROOT
        / "src"
        / "media_stack"
        / "services"
        / "apps"
        / "stack"
        / "cli"
        / "generate_secrets_main.py"
    )


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
                namespace="media-dev",
            )
        self.assertEqual(values["STACK_ADMIN_USERNAME"], "new-admin")
        self.assertEqual(values["STACK_ADMIN_PASSWORD"], "new-pass")

    def test_build_secret_values_empty_password_generates_random(self):
        """An empty admin password must be replaced with a strong random
        value — never the namespace literal. Random values are never equal
        to the namespace or any well-known default.
        """
        values = self.mod.build_secret_values(
            current={"STACK_ADMIN_USERNAME": "admin", "STACK_ADMIN_PASSWORD": ""},
            stack_admin_user="",
            pass_length=24,
            rotate_existing=False,
            namespace="media-dev",
        )
        pw = values["STACK_ADMIN_PASSWORD"]
        self.assertEqual(len(pw), 24)
        self.assertNotIn(pw, {"media-dev", "media-stack", "media-stack-admin",
                              "admin", ""})

    def test_build_secret_values_replaces_legacy_default_with_random(self):
        values = self.mod.build_secret_values(
            current={"STACK_ADMIN_USERNAME": "admin",
                     "STACK_ADMIN_PASSWORD": "media-stack-admin"},
            stack_admin_user="",
            pass_length=24,
            rotate_existing=False,
            namespace="media-dev",
        )
        pw = values["STACK_ADMIN_PASSWORD"]
        self.assertEqual(len(pw), 24)
        self.assertNotEqual(pw, "media-stack-admin")
        self.assertNotEqual(pw, "media-dev")

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
