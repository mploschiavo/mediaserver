import base64
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.controller_secret_reader_service import (  # noqa: E402
    ControllerSecretReaderConfig,
    ControllerSecretReaderService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, encoded: str, *, returncode: int = 0) -> None:
        self.encoded = encoded
        self.returncode = returncode

    def run(self, _args, **_kwargs):
        return _Result(self.returncode, self.encoded)


class ControllerSecretReaderServiceTests(unittest.TestCase):
    def test_read_secret_key_decodes_base64(self):
        encoded = base64.b64encode(b"abc123").decode("utf-8")
        svc = ControllerSecretReaderService(
            cfg=ControllerSecretReaderConfig(namespace="media-stack"),
            kube=_Kube(encoded),
        )
        self.assertEqual(svc.read_secret_key("media-stack-secrets", "TOKEN"), "abc123")

    def test_read_secret_key_returns_empty_on_failure(self):
        svc = ControllerSecretReaderService(
            cfg=ControllerSecretReaderConfig(namespace="media-stack"),
            kube=_Kube("", returncode=1),
        )
        self.assertEqual(svc.read_secret_key("media-stack-secrets", "TOKEN"), "")


if __name__ == "__main__":
    unittest.main()
