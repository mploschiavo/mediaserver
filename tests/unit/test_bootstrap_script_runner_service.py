import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_script_runner_service import (  # noqa: E402
    BootstrapScriptRunnerConfig,
    BootstrapScriptRunnerService,
)


class BootstrapScriptRunnerServiceTests(unittest.TestCase):
    def test_run_script_executes_shell_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            script = scripts / "ok.sh"
            script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            script.chmod(0o755)

            svc = BootstrapScriptRunnerService(cfg=BootstrapScriptRunnerConfig(root_dir=root))
            svc.run_script("ok.sh")

    def test_run_script_raises_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            script = scripts / "fail.sh"
            script.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
            script.chmod(0o755)

            svc = BootstrapScriptRunnerService(cfg=BootstrapScriptRunnerConfig(root_dir=root))
            with self.assertRaises(RuntimeError):
                svc.run_script("fail.sh")


if __name__ == "__main__":
    unittest.main()
