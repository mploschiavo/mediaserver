import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_secret_priming_service import (  # noqa: E402
    BootstrapSecretPrimingConfig,
    BootstrapSecretPrimingService,
)
from core.exceptions import ConfigError


class _Result:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Kube:
    cmd_prefix = ["kubectl"]

    def __init__(self, *, secret_exists: bool = True) -> None:
        self.secret_exists = secret_exists
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        cmd = list(args)
        self.calls.append(cmd)
        if cmd[:5] == ["-n", "media-stack", "get", "secret", "media-stack-secrets"]:
            return _Result(0 if self.secret_exists else 1, "ok")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/jellyseerr", "--"]:
            return _Result(0, "jellyseerr-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/tautulli", "--"]:
            return _Result(0, "tautulli-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/sabnzbd", "--"]:
            return _Result(0, "sab-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/sonarr", "--"]:
            return _Result(0, "sonarr-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/radarr", "--"]:
            return _Result(0, "radarr-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/lidarr", "--"]:
            return _Result(0, "lidarr-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/readarr", "--"]:
            return _Result(0, "readarr-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/prowlarr", "--"]:
            return _Result(0, "prowlarr-key\n")
        if cmd[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]:
            return _Result(0, "patched")
        return _Result(1, "", "unexpected command")


class BootstrapSecretPrimingServiceTests(unittest.TestCase):
    def test_primes_jellyseerr_and_tautulli_keys(self):
        kube = _Kube()
        svc = BootstrapSecretPrimingService(
            cfg=BootstrapSecretPrimingConfig(namespace="media-stack"),
            kube=kube,
            info=mock.Mock(),
            warn=mock.Mock(),
        )
        svc.prime_jellyseerr_api_key()
        svc.prime_tautulli_api_key()

        payloads = []
        for call in kube.calls:
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]:
                payloads.append(json.loads(call[-1]))
        self.assertIn({"stringData": {"JELLYSEERR_API_KEY": "jellyseerr-key"}}, payloads)
        self.assertIn({"stringData": {"TAUTULLI_API_KEY": "tautulli-key"}}, payloads)

    def test_primes_all_servarr_and_prowlarr_keys(self):
        kube = _Kube()
        svc = BootstrapSecretPrimingService(
            cfg=BootstrapSecretPrimingConfig(namespace="media-stack"),
            kube=kube,
            info=mock.Mock(),
            warn=mock.Mock(),
        )
        svc.prime_servarr_api_keys()

        patch_payloads = [
            json.loads(call[-1])
            for call in kube.calls
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]
        ]
        keys = {
            next(iter((payload.get("stringData") or {}).keys()))
            for payload in patch_payloads
            if payload.get("stringData")
        }
        self.assertIn("SONARR_API_KEY", keys)
        self.assertIn("RADARR_API_KEY", keys)
        self.assertIn("LIDARR_API_KEY", keys)
        self.assertIn("READARR_API_KEY", keys)
        self.assertIn("PROWLARR_API_KEY", keys)

    def test_primes_only_configured_arr_apps_from_resolved_bootstrap_config(self):
        kube = _Kube()
        with tempfile.TemporaryDirectory() as tmpdir:
            bootstrap_cfg = Path(tmpdir) / "resolved-bootstrap.json"
            bootstrap_cfg.write_text(
                json.dumps(
                    {
                        "arr_apps": [
                            {"name": "Radarr", "implementation": "radarr"},
                            {"name": "Lidarr", "implementation": "lidarr"},
                        ],
                        "prowlarr_url": "http://prowlarr:9696",
                    }
                ),
                encoding="utf-8",
            )
            svc = BootstrapSecretPrimingService(
                cfg=BootstrapSecretPrimingConfig(
                    namespace="media-stack",
                    bootstrap_config_file=bootstrap_cfg,
                ),
                kube=kube,
                info=mock.Mock(),
                warn=mock.Mock(),
            )
            svc.prime_servarr_api_keys()

        patch_payloads = [
            json.loads(call[-1])
            for call in kube.calls
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]
        ]
        keys = {
            next(iter((payload.get("stringData") or {}).keys()))
            for payload in patch_payloads
            if payload.get("stringData")
        }
        self.assertEqual(
            keys,
            {"RADARR_API_KEY", "LIDARR_API_KEY", "PROWLARR_API_KEY"},
        )

        exec_targets = [
            call[3]
            for call in kube.calls
            if call[:3] == ["-n", "media-stack", "exec"] and len(call) > 3
        ]
        self.assertIn("deploy/radarr", exec_targets)
        self.assertIn("deploy/lidarr", exec_targets)
        self.assertIn("deploy/prowlarr", exec_targets)
        self.assertNotIn("deploy/sonarr", exec_targets)
        self.assertNotIn("deploy/readarr", exec_targets)

    def test_skips_when_secret_missing(self):
        kube = _Kube(secret_exists=False)
        warn = mock.Mock()
        svc = BootstrapSecretPrimingService(
            cfg=BootstrapSecretPrimingConfig(namespace="media-stack"),
            kube=kube,
            info=mock.Mock(),
            warn=warn,
        )

        with mock.patch.dict("os.environ", {"SABNZBD_API_KEY": "env-sab-key"}, clear=False):
            svc.prime_sab_api_key()

        warning_messages = " ".join(call.args[0] for call in warn.call_args_list if call.args)
        self.assertIn("media-stack-secrets", warning_messages)
        patch_calls = [
            call
            for call in kube.calls
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]
        ]
        self.assertEqual(patch_calls, [])

    def test_invalid_bootstrap_config_fails_fast(self):
        kube = _Kube()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = Path(tmpdir) / "bootstrap.json"
            cfg_path.write_text("{not-json", encoding="utf-8")
            svc = BootstrapSecretPrimingService(
                cfg=BootstrapSecretPrimingConfig(
                    namespace="media-stack",
                    bootstrap_config_file=cfg_path,
                ),
                kube=kube,
                info=mock.Mock(),
                warn=mock.Mock(),
            )
            with self.assertRaises(ConfigError):
                svc.prime_servarr_api_keys()

    def test_missing_manifest_derived_targets_fails_fast(self):
        kube = _Kube()
        svc = BootstrapSecretPrimingService(
            cfg=BootstrapSecretPrimingConfig(namespace="media-stack"),
            kube=kube,
            info=mock.Mock(),
            warn=mock.Mock(),
        )
        with mock.patch(
            "cli.bootstrap_secret_priming_service.load_plugin_manifests",
            return_value=[],
        ):
            with self.assertRaises(ConfigError):
                svc.prime_servarr_api_keys()


if __name__ == "__main__":
    unittest.main()
