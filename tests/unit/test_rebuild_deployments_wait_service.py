import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_deployments_wait_service import (  # noqa: E402
    RebuildDeploymentsWaitConfig,
    RebuildDeploymentsWaitService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RebuildDeploymentsWaitServiceTests(unittest.TestCase):
    def test_wait_for_deployments_success(self):
        svc = RebuildDeploymentsWaitService(
            cfg=RebuildDeploymentsWaitConfig(
                namespace="media-stack",
                wait_timeout="10m",
                kubectl=["kubectl"],
            ),
            info=mock.Mock(),
            warn=mock.Mock(),
        )
        side_effect = [
            _Result(0, "sonarr\n"),  # list deploys
            _Result(0, "1"),  # replicas
            _Result(0, "ok"),  # rollout status
        ]
        with mock.patch("subprocess.run", side_effect=side_effect):
            svc.wait_for_deployments()


if __name__ == "__main__":
    unittest.main()
