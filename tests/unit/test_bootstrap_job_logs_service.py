import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.controller_job_logs_service import (  # noqa: E402
    ControllerJobLogsConfig,
    ControllerJobLogsService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, output: str, *, returncode: int = 0) -> None:
        self.output = output
        self.returncode = returncode

    def run(self, _args, **_kwargs):
        return _Result(self.returncode, self.output, "boom" if self.returncode else "")


class ControllerJobLogsServiceTests(unittest.TestCase):
    def test_capture_and_contains(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "job.log"
            svc = ControllerJobLogsService(
                cfg=ControllerJobLogsConfig(
                    namespace="media-stack",
                    job_name="media-stack-controller",
                    log_file=log_file,
                    tail_lines=5,
                ),
                kube=_Kube("one\ntwo\nspecial-marker\n"),
            )
            svc.capture_logs()
            self.assertTrue(log_file.exists())
            self.assertTrue(svc.log_contains("special-marker"))


if __name__ == "__main__":
    unittest.main()
