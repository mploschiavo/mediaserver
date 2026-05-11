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


class ControllerScriptRunnerServiceTests(unittest.TestCase):
    """Smoke tests for the unified runner under the controller-side
    config shape (no ``extra_env`` injection).

    ADR-0015 Phase 6 deleted the ``ControllerScriptRunnerService``
    shim; these tests exercise the unified
    :class:`ScriptRunnerService` directly with the controller-side
    config (``root_dir`` only).
    """

    def test_run_script_executes_shell_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "bin"
            scripts.mkdir(parents=True, exist_ok=True)
            script = scripts / "ok.sh"
            script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            script.chmod(0o755)

            svc = ScriptRunnerService(cfg=ScriptRunnerConfig(root_dir=root))
            svc.run_script("ok.sh")

    def test_run_script_raises_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "bin"
            scripts.mkdir(parents=True, exist_ok=True)
            script = scripts / "fail.sh"
            script.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
            script.chmod(0o755)

            svc = ScriptRunnerService(cfg=ScriptRunnerConfig(root_dir=root))
            with self.assertRaises(RuntimeError):
                svc.run_script("fail.sh")


if __name__ == "__main__":
    unittest.main()
