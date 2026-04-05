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
            command_text = " ".join(str(part) for part in cmd[5:])
            if "Users?api_key" in command_text:
                return _Result(0, "jellyfin-user-id\n")
            if "d.jellyfin" in command_text:
                return _Result(0, "jellyfin-key\n")
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


def _write_bootstrap_cfg(tmpdir: str, payload: dict[str, object]) -> Path:
    path = Path(tmpdir) / "resolved-bootstrap.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class BootstrapSecretPrimingServiceTests(unittest.TestCase):
    def test_primes_request_manager_and_analytics_keys(self):
        kube = _Kube()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = _write_bootstrap_cfg(
                tmpdir,
                {
                    "adapter_hooks": {
                        "bootstrap_job": {
                            "secret_priming_targets": {
                                "request_manager": {
                                    "env_key": "JELLYSEERR_API_KEY",
                                    "env_var": "JELLYSEERR_API_KEY",
                                    "deployment": "jellyseerr",
                                    "extract_command": "cat /tmp/key",
                                },
                                "analytics": {
                                    "env_key": "TAUTULLI_API_KEY",
                                    "env_var": "TAUTULLI_API_KEY",
                                    "deployment": "tautulli",
                                    "extract_command": "cat /tmp/key",
                                },
                            }
                        }
                    }
                },
            )
            svc = BootstrapSecretPrimingService(
                cfg=BootstrapSecretPrimingConfig(
                    namespace="media-stack",
                    bootstrap_config_file=cfg_path,
                ),
                kube=kube,
                info=mock.Mock(),
                warn=mock.Mock(),
            )
            svc.prime_request_manager_api_key()
            svc.prime_analytics_api_key()

        payloads = []
        for call in kube.calls:
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]:
                payloads.append(json.loads(call[-1]))
        self.assertIn({"stringData": {"JELLYSEERR_API_KEY": "jellyseerr-key"}}, payloads)
        self.assertIn({"stringData": {"TAUTULLI_API_KEY": "tautulli-key"}}, payloads)

    def test_primes_media_server_key_and_user_id(self):
        kube = _Kube()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = _write_bootstrap_cfg(
                tmpdir,
                {
                    "adapter_hooks": {
                        "bootstrap_job": {
                            "secret_priming_targets": {
                                "media_server_api_key": {
                                    "env_key": "JELLYFIN_API_KEY",
                                    "env_var": "JELLYFIN_API_KEY",
                                    "deployment": "jellyseerr",
                                    "extract_command": "node -e \"const d={jellyfin:{apiKey:'x'}}; process.stdout.write(String((((d.jellyfin||{}).apiKey)||'')).trim());\"",
                                },
                                "media_server_user_id": {
                                    "env_key": "JELLYFIN_USER_ID",
                                    "env_var": "JELLYFIN_USER_ID",
                                    "deployment": "jellyseerr",
                                    "extract_command": "node -e \"const d={jellyfin:{apiKey:'x'}}; http.get('http://jellyfin:8096/Users?api_key='+encodeURIComponent('x'));\"",
                                },
                            }
                        }
                    }
                },
            )
            svc = BootstrapSecretPrimingService(
                cfg=BootstrapSecretPrimingConfig(
                    namespace="media-stack",
                    bootstrap_config_file=cfg_path,
                ),
                kube=kube,
                info=mock.Mock(),
                warn=mock.Mock(),
            )
            svc.prime_media_server_api_key()
            svc.prime_media_server_user_id()

        payloads = [
            json.loads(call[-1])
            for call in kube.calls
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]
        ]
        self.assertIn({"stringData": {"JELLYFIN_API_KEY": "jellyfin-key"}}, payloads)
        self.assertIn({"stringData": {"JELLYFIN_USER_ID": "jellyfin-user-id"}}, payloads)

    def test_primes_all_configured_api_keys(self):
        kube = _Kube()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = _write_bootstrap_cfg(
                tmpdir,
                {
                    "adapter_hooks": {
                        "bootstrap_job": {
                            "arr_api_key_technologies": [
                                "sonarr",
                                "radarr",
                                "lidarr",
                                "readarr",
                                "prowlarr",
                            ]
                        }
                    }
                },
            )
            svc = BootstrapSecretPrimingService(
                cfg=BootstrapSecretPrimingConfig(
                    namespace="media-stack",
                    bootstrap_config_file=cfg_path,
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
        self.assertIn("SONARR_API_KEY", keys)
        self.assertIn("RADARR_API_KEY", keys)
        self.assertIn("LIDARR_API_KEY", keys)
        self.assertIn("READARR_API_KEY", keys)
        self.assertIn("PROWLARR_API_KEY", keys)

    def test_primes_only_configured_api_key_apps(self):
        kube = _Kube()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = _write_bootstrap_cfg(
                tmpdir,
                {
                    "adapter_hooks": {
                        "bootstrap_job": {
                            "arr_api_key_technologies": [
                                "radarr",
                                "lidarr",
                                "prowlarr",
                            ]
                        }
                    }
                },
            )
            svc = BootstrapSecretPrimingService(
                cfg=BootstrapSecretPrimingConfig(
                    namespace="media-stack",
                    bootstrap_config_file=cfg_path,
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
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = _write_bootstrap_cfg(
                tmpdir,
                {
                    "adapter_hooks": {
                        "bootstrap_job": {
                            "secret_priming_targets": {
                                "usenet_client": {
                                    "env_key": "SABNZBD_API_KEY",
                                    "env_var": "SABNZBD_API_KEY",
                                    "deployment": "sabnzbd",
                                    "extract_command": "cat /tmp/key",
                                }
                            }
                        }
                    }
                },
            )
            svc = BootstrapSecretPrimingService(
                cfg=BootstrapSecretPrimingConfig(
                    namespace="media-stack",
                    bootstrap_config_file=cfg_path,
                ),
                kube=kube,
                info=mock.Mock(),
                warn=warn,
            )

            with mock.patch.dict("os.environ", {"SABNZBD_API_KEY": "env-sab-key"}, clear=False):
                svc.prime_usenet_client_api_key()

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

    def test_missing_api_key_technologies_fails_fast(self):
        kube = _Kube()
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_path = _write_bootstrap_cfg(
                tmpdir,
                {"adapter_hooks": {"bootstrap_job": {}}},
            )
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


if __name__ == "__main__":
    unittest.main()
