import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_deployment_ops_service import (  # noqa: E402
    BootstrapDeploymentOpsConfig,
    BootstrapDeploymentOpsService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, *, exists: bool = True) -> None:
        self.exists = exists
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        cmd = list(args)
        self.calls.append(cmd)
        if cmd[:3] == ["-n", "media-stack", "get"]:
            return _Result(0 if self.exists else 1)
        if cmd[:5] == ["-n", "media-stack", "rollout", "restart", "deployment/jellyfin"]:
            return _Result(0)
        if cmd[:5] == ["-n", "media-stack", "rollout", "status", "deployment/jellyfin"]:
            return _Result(0)
        return _Result(1, "", "unexpected command")


class BootstrapDeploymentOpsServiceTests(unittest.TestCase):
    def test_restart_if_exists_skips_when_missing(self):
        kube = _Kube(exists=False)
        info = mock.Mock()
        svc = BootstrapDeploymentOpsService(
            cfg=BootstrapDeploymentOpsConfig(namespace="media-stack"),
            kube=kube,
            info=info,
        )
        svc.restart_deployment_if_exists("jellyfin", timeout_seconds=30)
        self.assertTrue(any("skipping restart" in c.args[0] for c in info.call_args_list if c.args))

    def test_restart_deployment_runs_rollout(self):
        kube = _Kube(exists=True)
        svc = BootstrapDeploymentOpsService(
            cfg=BootstrapDeploymentOpsConfig(namespace="media-stack"),
            kube=kube,
            info=mock.Mock(),
        )
        svc.restart_deployment("jellyfin", timeout_seconds=30)
        called = [" ".join(call) for call in kube.calls]
        self.assertTrue(any("rollout restart deployment/jellyfin" in c for c in called))
        self.assertTrue(
            any("rollout status deployment/jellyfin --timeout=30s" in c for c in called)
        )


if __name__ == "__main__":
    unittest.main()
