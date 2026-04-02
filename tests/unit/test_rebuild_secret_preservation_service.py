import base64
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.kubernetes.services.rebuild_secret_preservation_service import (  # noqa: E402
    RebuildSecretPreservationConfig,
    RebuildSecretPreservationService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RebuildSecretPreservationServiceTests(unittest.TestCase):
    def test_backup_existing_values_reads_and_decodes(self):
        encoded = base64.b64encode(b"secret").decode("utf-8")
        payload = {"data": {"STACK_ADMIN_PASSWORD": encoded}}
        svc = RebuildSecretPreservationService(
            cfg=RebuildSecretPreservationConfig(
                namespace="media-stack",
                secret_name="media-stack-secrets",
            ),
            info=mock.Mock(),
            run_kube=mock.Mock(return_value=_Result(0, json.dumps(payload))),
        )
        values = svc.backup_existing_values("1")
        self.assertEqual(values.get("STACK_ADMIN_PASSWORD"), "secret")

    def test_restore_values_noop_when_empty(self):
        info = mock.Mock()
        svc = RebuildSecretPreservationService(
            cfg=RebuildSecretPreservationConfig(
                namespace="media-stack",
                secret_name="media-stack-secrets",
            ),
            info=info,
            run_kube=mock.Mock(),
        )
        svc.restore_values({})
        self.assertTrue(
            any("No preserved secret values" in c.args[0] for c in info.call_args_list if c.args)
        )


if __name__ == "__main__":
    unittest.main()
