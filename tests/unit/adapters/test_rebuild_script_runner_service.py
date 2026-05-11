import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.script_runner_service import (  # noqa: E402
    ScriptRunnerConfig,
    ScriptRunnerService,
)


class DeployScriptRunnerServiceTests(unittest.TestCase):
    """Smoke tests for the unified runner under the deploy-side config
    shape (namespace passed via ``extra_env``).

    ADR-0015 Phase 6 deleted the ``DeployScriptRunnerService`` shim;
    these tests exercise the unified :class:`ScriptRunnerService`
    with the same ``NAMESPACE`` env injection that the deploy
    pipeline does in :class:`DeployServiceFactoryBundle`.
    """

    def test_run_script_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "bin"
            scripts.mkdir(parents=True, exist_ok=True)
            file = scripts / "ok.sh"
            file.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            file.chmod(0o755)

            svc = ScriptRunnerService(
                cfg=ScriptRunnerConfig(
                    root_dir=root, extra_env={"NAMESPACE": "media-stack"},
                )
            )
            svc.run_script("ok.sh")

    def test_run_script_failure_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "bin"
            scripts.mkdir(parents=True, exist_ok=True)
            file = scripts / "bad.sh"
            file.write_text("#!/usr/bin/env bash\nexit 4\n", encoding="utf-8")
            file.chmod(0o755)

            svc = ScriptRunnerService(
                cfg=ScriptRunnerConfig(
                    root_dir=root, extra_env={"NAMESPACE": "media-stack"},
                )
            )
            with self.assertRaises(RuntimeError):
                svc.run_script("bad.sh")


if __name__ == "__main__":
    unittest.main()
