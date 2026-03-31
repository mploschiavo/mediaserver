import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_ingress_service import (  # noqa: E402
    RebuildIngressConfig,
    RebuildIngressService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RebuildIngressServiceTests(unittest.TestCase):
    def test_auto_pick_prefers_public(self):
        svc = RebuildIngressService(
            cfg=RebuildIngressConfig(namespace="media-stack", ingress_class="auto", kubectl=["kubectl"]),
            info=mock.Mock(),
            warn=mock.Mock(),
            run_script=mock.Mock(),
        )
        with mock.patch("subprocess.run", return_value=_Result(0, "nginx\npublic\n")):
            self.assertEqual(svc.pick_ingress_class(), "public")

    def test_patch_returns_false_when_no_classes(self):
        svc = RebuildIngressService(
            cfg=RebuildIngressConfig(namespace="media-stack", ingress_class="auto", kubectl=["kubectl"]),
            info=mock.Mock(),
            warn=mock.Mock(),
            run_script=mock.Mock(),
        )
        with mock.patch("subprocess.run", return_value=_Result(0, "")):
            self.assertFalse(svc.patch_ingress_class())


if __name__ == "__main__":
    unittest.main()
