import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.deploy_script_runner_service import (  # noqa: E402
    DeployScriptRunnerConfig,
    DeployScriptRunnerService,
)


class DeployScriptRunnerServiceTests(unittest.TestCase):
    def test_run_script_executes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            file = scripts / "ok.sh"
            file.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
            file.chmod(0o755)

            svc = DeployScriptRunnerService(
                cfg=DeployScriptRunnerConfig(root_dir=root, namespace="media-stack")
            )
            svc.run_script("ok.sh")

    def test_run_script_failure_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scripts = root / "scripts"
            scripts.mkdir(parents=True, exist_ok=True)
            file = scripts / "bad.sh"
            file.write_text("#!/usr/bin/env bash\nexit 4\n", encoding="utf-8")
            file.chmod(0o755)

            svc = DeployScriptRunnerService(
                cfg=DeployScriptRunnerConfig(root_dir=root, namespace="media-stack")
            )
            with self.assertRaises(RuntimeError):
                svc.run_script("bad.sh")


if __name__ == "__main__":
    unittest.main()
