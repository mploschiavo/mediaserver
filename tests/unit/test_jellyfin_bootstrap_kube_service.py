import base64
import json
import unittest
from unittest.mock import patch

from media_stack.services.apps.jellyfin.cli import jellyfin_controller_kube_service as svc


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class JellyfinBootstrapKubeServiceTests(unittest.TestCase):
    def test_get_secret_decodes_values(self):
        payload = {
            "data": {
                "STACK_ADMIN_USERNAME": base64.b64encode(b"admin").decode("ascii"),
                "STACK_ADMIN_PASSWORD": base64.b64encode(b"secret").decode("ascii"),
            }
        }
        with patch.object(
            svc, "run_cmd", return_value=_Proc(returncode=0, stdout=json.dumps(payload))
        ):
            values = svc.get_secret(["kubectl"], "media-stack", "media-stack-secrets")
        self.assertEqual(values["STACK_ADMIN_USERNAME"], "admin")
        self.assertEqual(values["STACK_ADMIN_PASSWORD"], "secret")

    def test_patch_secret_invokes_kubectl_patch(self):
        calls = []

        def _run_cmd(cmd, check=True):
            calls.append((cmd, check))
            return _Proc(returncode=0, stdout="ok")

        with patch.object(svc, "run_cmd", side_effect=_run_cmd):
            svc.patch_secret(
                ["kubectl"],
                "media-stack",
                "media-stack-secrets",
                {"JELLYFIN_API_KEY": "abc"},
            )

        self.assertTrue(calls)
        cmd, check = calls[0]
        self.assertTrue(check)
        self.assertIn("patch", cmd)
        self.assertIn("secret", cmd)
        self.assertIn("media-stack-secrets", cmd)


if __name__ == "__main__":
    unittest.main()
