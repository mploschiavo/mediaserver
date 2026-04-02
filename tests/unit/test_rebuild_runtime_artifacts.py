import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.rebuild_and_bootstrap_main import RebuildBootstrapRunner  # noqa: E402
from cli.rebuild_cli_config_service import RebuildBootstrapConfig  # noqa: E402
from core.subprocess_utils import CommandResult  # noqa: E402


class RebuildRuntimeArtifactsTests(unittest.TestCase):
    def _cfg(self, root_dir: Path, *, platform_target: str = "k8s") -> RebuildBootstrapConfig:
        config_file = root_dir / "bootstrap-config.json"
        config_file.write_text("{}\n", encoding="utf-8")
        return RebuildBootstrapConfig(
            root_dir=root_dir,
            platform_target=platform_target,
            config_file=config_file,
        )

    def test_initialize_runtime_artifacts_writes_run_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            runner = RebuildBootstrapRunner(cfg=self._cfg(root_dir))

            runner._initialize_runtime_artifacts()

            self.assertIsNotNone(runner.runtime_artifacts_root)
            context_file = (
                runner.runtime_artifacts_root / "shared" / "run-context.json"  # type: ignore[arg-type]
            )
            self.assertTrue(context_file.exists())
            payload = json.loads(context_file.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("platform_target"), "k8s")
            self.assertEqual(payload.get("namespace"), "media-stack")

    def test_run_kubectl_captures_resolved_k8s_apply_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_dir = Path(tmp)
            runner = RebuildBootstrapRunner(cfg=self._cfg(root_dir))
            runner.runtime_artifacts_root = root_dir / ".state" / "runtime-artifacts" / "run"
            runner.runtime_artifacts_root.mkdir(parents=True, exist_ok=True)
            runner.kube = mock.Mock()
            runner.kube.run.return_value = CommandResult(
                args=["apply", "-f", "-"],
                returncode=0,
                stdout="",
                stderr="",
            )

            runner._run_kubectl(
                ["apply", "-f", "-"],
                check=False,
                input_text="apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: sample\n",
            )

            manifest_file = (
                runner.runtime_artifacts_root / "kubernetes" / "applied-manifests" / "001.yaml"
            )
            metadata_file = (
                runner.runtime_artifacts_root / "kubernetes" / "applied-manifests" / "001.meta.json"
            )
            self.assertTrue(manifest_file.exists())
            self.assertTrue(metadata_file.exists())
            self.assertIn("kind: ConfigMap", manifest_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
