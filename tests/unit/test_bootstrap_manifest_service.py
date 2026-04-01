import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_manifest_service import (  # noqa: E402
    BootstrapManifestConfig,
    BootstrapManifestService,
)
from core.exceptions import ConfigError  # noqa: E402


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, pvc_status: dict[str, bool]) -> None:
        self.pvc_status = dict(pvc_status)
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        cmd = list(args)
        self.calls.append(cmd)

        if cmd[:2] == ["apply", "-f"]:
            return _Result(0, "applied")
        if cmd[:4] == ["-n", "media-stack", "get", "pvc"] and len(cmd) >= 5:
            pvc_name = cmd[4]
            return _Result(0 if self.pvc_status.get(pvc_name, False) else 1, "ok")
        return _Result(1, "", "unexpected command")


class BootstrapManifestServiceTests(unittest.TestCase):
    @staticmethod
    def _service(root_dir: Path, kube: _Kube) -> BootstrapManifestService:
        return BootstrapManifestService(
            cfg=BootstrapManifestConfig(
                namespace="media-stack",
                root_dir=root_dir,
                prepare_host_root="/srv/media-stack",
                bootstrap_runner_image="registry.example/bootstrap:latest",
                job_config_file=root_dir / "job-config.json",
            ),
            kube=kube,
            info=lambda _msg: None,
            warn=lambda _msg: None,
        )

    def test_extract_pvc_names_from_manifest(self):
        manifest = """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: media-stack-config-radarr
spec: {}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sonarr
spec: {}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  labels:
    app: media-stack
  name: media-stack-media
spec: {}
"""
        names = BootstrapManifestService._extract_pvc_names_from_manifest(manifest)
        self.assertEqual(names, ["media-stack-config-radarr", "media-stack-media"])

    def test_ensure_bootstrap_pvc_prereqs_uses_discovered_manifest_pvcs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            (root_dir / "k8s").mkdir(parents=True, exist_ok=True)
            (root_dir / "k8s" / "storage-pvc.yaml").write_text(
                """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: custom-config-radarr
spec: {}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: custom-media
spec: {}
""",
                encoding="utf-8",
            )

            kube = _Kube(
                pvc_status={
                    "custom-config-radarr": True,
                    "custom-media": True,
                }
            )
            service = self._service(root_dir, kube)
            service.ensure_bootstrap_pvc_prereqs()

            queried = [
                call[4]
                for call in kube.calls
                if call[:4] == ["-n", "media-stack", "get", "pvc"] and len(call) >= 5
            ]
            self.assertEqual(queried, ["custom-config-radarr", "custom-media"])

    def test_ensure_bootstrap_pvc_prereqs_raises_for_missing_discovered_pvc(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root_dir = Path(tmpdir)
            (root_dir / "k8s").mkdir(parents=True, exist_ok=True)
            (root_dir / "k8s" / "storage-pvc.yaml").write_text(
                """
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: custom-config-radarr
spec: {}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: custom-media
spec: {}
""",
                encoding="utf-8",
            )

            kube = _Kube(
                pvc_status={
                    "custom-config-radarr": True,
                    "custom-media": False,
                }
            )
            service = self._service(root_dir, kube)
            with self.assertRaises(ConfigError):
                service.ensure_bootstrap_pvc_prereqs()


if __name__ == "__main__":
    unittest.main()
