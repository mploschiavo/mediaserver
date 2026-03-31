import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_namespace_service import (  # noqa: E402
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
            cfg=RebuildNamespaceConfig(namespace="media-stack", kubectl=["kubectl"]),
            info=mock.Mock(),
            run_kubectl=mock.Mock(),
        )
        self.assertFalse(svc.delete_namespace_optional("0"))

    def test_delete_namespace_optional_deletes_when_exists(self):
        info = mock.Mock()
        run_kubectl = mock.Mock(return_value=_Result(0))
        svc = RebuildNamespaceService(
            cfg=RebuildNamespaceConfig(namespace="media-stack", kubectl=["kubectl"]),
            info=info,
            run_kubectl=run_kubectl,
        )
        with mock.patch("subprocess.run", side_effect=[_Result(0), _Result(1)]):
            self.assertTrue(svc.delete_namespace_optional("1"))
        run_kubectl.assert_called_once()


if __name__ == "__main__":
    unittest.main()
