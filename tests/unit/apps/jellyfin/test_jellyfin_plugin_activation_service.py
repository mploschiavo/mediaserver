import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.services.apps.jellyfin.cli.jellyfin_plugin_activation_service import (  # noqa: E402
    JellyfinPluginActivationConfig,
    JellyfinPluginActivationService,
)


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, plugins_payload: str, returncode: int = 0) -> None:
        self.plugins_payload = plugins_payload
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        cmd = list(args)
        self.calls.append(cmd)
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/jellyfin", "--"]:
            return _Result(self.returncode, self.plugins_payload)
        return _Result(1, "", "unexpected command")


class JellyfinPluginActivationServiceTests(unittest.TestCase):
    def test_restarts_when_plugins_need_restart(self):
        kube = _Kube('[{"Status":"Restart"},{"Status":"Disabled"}]')
        restart = mock.Mock()
        svc = JellyfinPluginActivationService(
            cfg=JellyfinPluginActivationConfig(namespace="media-stack"),
            kube=kube,
            info=mock.Mock(),
            warn=mock.Mock(),
            deployment_exists=lambda name: name == "jellyfin",
            restart_deployment=restart,
            read_secret_key=lambda _secret, _key: "jelly-key",
        )
        svc.activate_plugins_if_needed()
        restart.assert_called_once_with("jellyfin", 300)

    def test_skips_when_no_api_key(self):
        kube = _Kube('[{"Status":"Restart"}]')
        restart = mock.Mock()
        svc = JellyfinPluginActivationService(
            cfg=JellyfinPluginActivationConfig(namespace="media-stack"),
            kube=kube,
            info=mock.Mock(),
            warn=mock.Mock(),
            deployment_exists=lambda _name: True,
            restart_deployment=restart,
            read_secret_key=lambda _secret, _key: "",
        )
        svc.activate_plugins_if_needed()
        restart.assert_not_called()
        self.assertEqual(kube.calls, [])


if __name__ == "__main__":
    unittest.main()
