import importlib.util
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "sync_unpackerr_keys", ROOT / "scripts" / "cli" / "sync_unpackerr_keys_main.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules["sync_unpackerr_keys"] = MODULE
SPEC.loader.exec_module(MODULE)

from core.subprocess_utils import CommandResult  # noqa: E402


class FakeKube:
    def __init__(self, keys: dict[str, str], *, unpackerr_replicas: int | None = None) -> None:
        self.keys = keys
        self.unpackerr_replicas = unpackerr_replicas
        self.calls: list[list[str]] = []

    def run(self, args, check=True, env=None, timeout=None):
        del check, env, timeout
        args = list(args)
        self.calls.append(args)

        joined = " ".join(args)
        for app in ("sonarr", "radarr", "lidarr", "readarr", "prowlarr"):
            if f"deploy/{app}" in joined:
                key = self.keys.get(app, "")
                xml = f"<Config><ApiKey>{key}</ApiKey></Config>"
                return CommandResult(args=args, returncode=0, stdout=xml, stderr="")

        if args[:2] == ["apply", "-f"]:
            return CommandResult(
                args=args,
                returncode=0,
                stdout="secret/media-stack-secrets configured\n",
                stderr="",
            )

        if args[:5] == ["-n", "media-stack", "get", "deploy/unpackerr", "-o"]:
            if self.unpackerr_replicas is None:
                return CommandResult(args=args, returncode=1, stdout="", stderr="not found")
            return CommandResult(
                args=args,
                returncode=0,
                stdout=str(self.unpackerr_replicas),
                stderr="",
            )

        if args[:5] == ["-n", "media-stack", "rollout", "restart", "deploy/unpackerr"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")

        if args[:5] == ["-n", "media-stack", "rollout", "status", "deploy/unpackerr"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")

        raise AssertionError(f"Unexpected call: {args}")


class SyncUnpackerrKeysTests(unittest.TestCase):
    def test_parse_config_defaults(self):
        cfg = MODULE.parse_config([])
        self.assertEqual(cfg.namespace, "media-stack")
        self.assertEqual(cfg.secret_name, "media-stack-secrets")

    def test_service_updates_secret_with_all_keys(self):
        kube = FakeKube(
            {
                "sonarr": "S",
                "radarr": "R",
                "lidarr": "L",
                "readarr": "B",
                "prowlarr": "P",
            }
        )
        service = MODULE.SyncUnpackerrKeysService(
            cfg=MODULE.SyncUnpackerrKeysConfig(namespace="media-stack"),
            kube=kube,
            logger=logging.getLogger("test.sync_unpackerr"),
        )
        with mock.patch("builtins.print"):
            rc = service.run()
        self.assertEqual(rc, 0)
        apply_calls = [call for call in kube.calls if call[:2] == ["apply", "-f"]]
        self.assertEqual(len(apply_calls), 1)
        rollout_calls = [
            call for call in kube.calls if call[:5] == ["-n", "media-stack", "rollout", "restart", "deploy/unpackerr"]
        ]
        self.assertEqual(len(rollout_calls), 0)

    def test_service_restarts_unpackerr_when_deployed_and_active(self):
        kube = FakeKube(
            {
                "sonarr": "S",
                "radarr": "R",
                "lidarr": "L",
                "readarr": "B",
                "prowlarr": "P",
            },
            unpackerr_replicas=1,
        )
        service = MODULE.SyncUnpackerrKeysService(
            cfg=MODULE.SyncUnpackerrKeysConfig(namespace="media-stack"),
            kube=kube,
            logger=logging.getLogger("test.sync_unpackerr"),
        )
        with mock.patch("builtins.print"):
            rc = service.run()
        self.assertEqual(rc, 0)
        rollout_restart = [
            call for call in kube.calls if call[:5] == ["-n", "media-stack", "rollout", "restart", "deploy/unpackerr"]
        ]
        rollout_status = [
            call for call in kube.calls if call[:5] == ["-n", "media-stack", "rollout", "status", "deploy/unpackerr"]
        ]
        self.assertEqual(len(rollout_restart), 1)
        self.assertEqual(len(rollout_status), 1)

    def test_service_fails_when_key_missing(self):
        kube = FakeKube(
            {
                "sonarr": "S",
                "radarr": "",
                "lidarr": "L",
                "readarr": "B",
                "prowlarr": "P",
            }
        )
        service = MODULE.SyncUnpackerrKeysService(
            cfg=MODULE.SyncUnpackerrKeysConfig(namespace="media-stack"),
            kube=kube,
            logger=logging.getLogger("test.sync_unpackerr"),
        )
        with self.assertRaisesRegex(Exception, "One or more API keys were empty"):
            service.run()


if __name__ == "__main__":
    unittest.main()
