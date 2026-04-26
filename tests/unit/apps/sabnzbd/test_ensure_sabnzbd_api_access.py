import importlib.util
import logging
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location(
    "ensure_sabnzbd_api_access",
    ROOT
    / "src"
    / "media_stack"
    / "services"
    / "apps"
    / "sabnzbd"
    / "cli"
    / "ensure_sabnzbd_api_access_main.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class FakeKubectlClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if not self._responses:
            raise AssertionError("Unexpected extra kubectl call")
        return self._responses.pop(0)


class EnsureSabApiAccessServiceTests(unittest.TestCase):
    def _logger(self):
        logger = logging.getLogger("test.ensure_sab")
        logger.handlers = []
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        return logger

    def test_skips_when_deployment_missing(self):
        cfg = MODULE.SabnzbdApiAccessConfig()
        kube = FakeKubectlClient(
            [
                CommandResult(
                    args=["kubectl", "get", "deploy", "sabnzbd"],
                    returncode=1,
                    stdout="",
                    stderr="not found",
                )
            ]
        )
        service = MODULE.SabnzbdApiAccessService(cfg=cfg, kube=kube, logger=self._logger())
        rc = service.run()
        self.assertEqual(rc, 0)
        self.assertEqual(len(kube.calls), 1)

    def test_does_not_restart_when_reconcile_unchanged(self):
        cfg = MODULE.SabnzbdApiAccessConfig()
        kube = FakeKubectlClient(
            [
                CommandResult(args=[], returncode=0, stdout="", stderr=""),  # deployment exists
                CommandResult(
                    args=[], returncode=0, stdout="", stderr=""
                ),  # initial rollout status
                CommandResult(  # reconcile output
                    args=[],
                    returncode=0,
                    stdout=(
                        "__CHANGED__=0\n"
                        "__HOST_WHITELIST__=a,b\n"
                        "__LOCAL_RANGES__=10.0.0.0/8\n"
                        "__DOWNLOAD_DIR__=/data/usenet/incomplete\n"
                        "__COMPLETE_DIR__=/data/usenet/completed\n"
                        "__AUTO_BROWSER__=0\n"
                    ),
                    stderr="",
                ),
            ]
        )
        service = MODULE.SabnzbdApiAccessService(cfg=cfg, kube=kube, logger=self._logger())
        rc = service.run()
        self.assertEqual(rc, 0)
        self.assertEqual(len(kube.calls), 3)

    def test_restarts_when_changed_marker_missing(self):
        cfg = MODULE.SabnzbdApiAccessConfig()
        kube = FakeKubectlClient(
            [
                CommandResult(args=[], returncode=0, stdout="", stderr=""),  # deployment exists
                CommandResult(
                    args=[], returncode=0, stdout="", stderr=""
                ),  # initial rollout status
                CommandResult(  # reconcile output missing __CHANGED__
                    args=[],
                    returncode=0,
                    stdout=(
                        "__HOST_WHITELIST__=a,b\n"
                        "__LOCAL_RANGES__=10.0.0.0/8\n"
                        "__DOWNLOAD_DIR__=/data/usenet/incomplete\n"
                        "__COMPLETE_DIR__=/data/usenet/completed\n"
                        "__AUTO_BROWSER__=0\n"
                    ),
                    stderr="",
                ),
                CommandResult(args=[], returncode=0, stdout="", stderr=""),  # rollout restart
                CommandResult(args=[], returncode=0, stdout="", stderr=""),  # rollout status
            ]
        )
        service = MODULE.SabnzbdApiAccessService(cfg=cfg, kube=kube, logger=self._logger())
        rc = service.run()
        self.assertEqual(rc, 0)
        self.assertEqual(len(kube.calls), 5)
        restart_call = kube.calls[3][0]
        self.assertIn("restart", restart_call)


if __name__ == "__main__":
    unittest.main()
