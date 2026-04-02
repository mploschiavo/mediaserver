import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from core.platforms.kubernetes.services.rebuild_namespace_service import (  # noqa: E402
    RebuildNamespaceConfig,
    RebuildNamespaceService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RebuildNamespaceServiceTests(unittest.TestCase):
    def test_delete_namespace_optional_returns_false_when_disabled(self):
        svc = RebuildNamespaceService(
            cfg=RebuildNamespaceConfig(namespace="media-stack"),
            info=mock.Mock(),
            run_kube=mock.Mock(),
        )
        self.assertFalse(svc.delete_namespace_optional("0"))

    def test_delete_namespace_optional_deletes_when_exists(self):
        info = mock.Mock()
        run_kube = mock.Mock(side_effect=[_Result(0), _Result(0), _Result(1)])
        svc = RebuildNamespaceService(
            cfg=RebuildNamespaceConfig(namespace="media-stack"),
            info=info,
            run_kube=run_kube,
        )
        self.assertTrue(svc.delete_namespace_optional("1"))
        self.assertGreaterEqual(run_kube.call_count, 2)


if __name__ == "__main__":
    unittest.main()
