import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_smoke_test_service import RebuildSmokeTestService  # noqa: E402


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RebuildSmokeTestServiceTests(unittest.TestCase):
    def test_detects_ip_and_runs_script(self):
        run_script = mock.Mock()
        svc = RebuildSmokeTestService(
            namespace="media-stack",
            node_ip="",
            info=mock.Mock(),
            warn=mock.Mock(),
            run_script=run_script,
        )
        with mock.patch("subprocess.run", return_value=_Result(0, "192.168.1.10\n")):
            resolved = svc.run_smoke_test()
        self.assertEqual(resolved, "192.168.1.10")
        run_script.assert_called_once()


if __name__ == "__main__":
    unittest.main()
