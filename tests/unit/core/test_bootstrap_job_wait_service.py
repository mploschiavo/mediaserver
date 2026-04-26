import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.controller_job_wait_service import (  # noqa: E402
    ControllerJobWaitConfig,
    ControllerJobWaitService,
)
from media_stack.core.exceptions import KubernetesError  # noqa: E402


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(
        self,
        *,
        job_exists: bool = True,
        succeeded: bool = True,
        log_output: str = "",
    ) -> None:
        self.job_exists = job_exists
        self.succeeded = succeeded
        self.log_output = log_output
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        cmd = list(args)
        self.calls.append(cmd)

        if (
            len(cmd) >= 7
            and cmd[2] == "get"
            and cmd[3] == "job"
            and cmd[5].startswith("-o")
            and "custom-columns" in cmd[6]
        ):
            return _Result(0, "")
        if (
            len(cmd) >= 7
            and cmd[2] == "get"
            and cmd[3] == "pods"
            and cmd[5] == "-l"
            and cmd[6] == "app=media-stack-controller"
        ):
            return _Result(0, "")
        if (
            len(cmd) >= 6
            and cmd[2] == "get"
            and cmd[3] == "job"
            and cmd[5] == "-o"
            and cmd[6] == "json"
        ):
            if not self.job_exists:
                return _Result(1, "", "not found")
            status = {"succeeded": 1} if self.succeeded else {}
            return _Result(0, json.dumps({"status": status}))
        if (
            len(cmd) >= 7
            and cmd[2] == "get"
            and cmd[3] == "pods"
            and cmd[5] == "-l"
            and cmd[6] == "job-name=media-stack-controller"
        ):
            return _Result(0, json.dumps({"items": []}))
        if len(cmd) >= 5 and cmd[2] == "logs" and cmd[3] == "job/media-stack-controller":
            return _Result(0, self.log_output)
        return _Result(0, "")


class ControllerJobWaitServiceTests(unittest.TestCase):
    def _svc(self, kube: _Kube) -> ControllerJobWaitService:
        return ControllerJobWaitService(
            cfg=ControllerJobWaitConfig(
                namespace="media-stack",
                timeout_seconds=60,
                timeout_raw="1m",
                heartbeat_interval=15,
                job_discovery_grace_seconds=0,
                job_missing_timeout_seconds=0,
            ),
            kube=kube,
            info=mock.Mock(),
            warn=mock.Mock(),
            now=lambda: 0,
            sleep=lambda _seconds: None,
        )

    def test_wait_for_job_returns_when_succeeded(self):
        svc = self._svc(_Kube(job_exists=True, succeeded=True))
        svc.wait_for_job(job_name="media-stack-controller", selector="app=media-stack-controller")

    def test_wait_for_job_raises_when_job_missing(self):
        svc = self._svc(_Kube(job_exists=False, succeeded=False))
        with self.assertRaises(KubernetesError):
            svc.wait_for_job(job_name="media-stack-controller", selector="app=media-stack-controller")

    def test_wait_for_job_returns_when_success_marker_appears_in_logs(self):
        times = iter([0, 0, 15, 15])
        svc = ControllerJobWaitService(
            cfg=ControllerJobWaitConfig(
                namespace="media-stack",
                timeout_seconds=60,
                timeout_raw="1m",
                heartbeat_interval=15,
                job_discovery_grace_seconds=0,
                job_missing_timeout_seconds=0,
            ),
            kube=_Kube(
                job_exists=True,
                succeeded=False,
                log_output="[2026-04-05T22:42:24+0000] [OK] Bootstrap completed successfully",
            ),
            info=mock.Mock(),
            warn=mock.Mock(),
            now=lambda: next(times),
            sleep=lambda _seconds: None,
        )

        svc.wait_for_job(job_name="media-stack-controller", selector="app=media-stack-controller")


if __name__ == "__main__":
    unittest.main()
