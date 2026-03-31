import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

SPEC = importlib.util.spec_from_file_location(
    "run_bootstrap_job",
    ROOT / "scripts" / "cli" / "run_bootstrap_job_main.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class _FakeKube:
    cmd_prefix = ["kubectl"]

    def run(self, *_args, **_kwargs):
        raise AssertionError("kube.run should not be used in this unit test")


class _FakeResult:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _RecordingKube:
    cmd_prefix = ["kubectl"]

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        self.calls.append(list(args))
        cmd = list(args)
        if cmd[:5] == ["-n", "media-stack", "get", "secret", "media-stack-secrets"]:
            return _FakeResult(0, "ok")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/jellyseerr", "--"]:
            return _FakeResult(0, "jellyseerr-key\n")
        if cmd[:5] == ["-n", "media-stack", "exec", "deploy/tautulli", "--"]:
            return _FakeResult(0, "tautulli-key\n")
        if cmd[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]:
            return _FakeResult(0, "patched")
        return _FakeResult(1, "", "unexpected command")


class RunBootstrapJobRunnerUnitTests(unittest.TestCase):
    def test_timeout_seconds_parsing(self):
        self.assertEqual(
            MODULE.RunBootstrapJobConfig(
                namespace="media-stack",
                timeout_raw="10m",
                heartbeat_interval=15,
                job_log_tail_lines=120,
                alert_webhook_url="",
                prepare_host_root="/srv/media-stack",
                ingress_name="media-stack-ingress",
                bootstrap_runner_image="registry.example/bootstrap:latest",
                root_dir=ROOT,
                config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
                skip_qbit_ensure=False,
                skip_sab_ensure=False,
            ).timeout_seconds,
            600,
        )
        self.assertEqual(
            MODULE.RunBootstrapJobConfig(
                namespace="media-stack",
                timeout_raw="90s",
                heartbeat_interval=15,
                job_log_tail_lines=120,
                alert_webhook_url="",
                prepare_host_root="/srv/media-stack",
                ingress_name="media-stack-ingress",
                bootstrap_runner_image="registry.example/bootstrap:latest",
                root_dir=ROOT,
                config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
                skip_qbit_ensure=False,
                skip_sab_ensure=False,
            ).timeout_seconds,
            90,
        )
        self.assertEqual(
            MODULE.RunBootstrapJobConfig(
                namespace="media-stack",
                timeout_raw="2h",
                heartbeat_interval=15,
                job_log_tail_lines=120,
                alert_webhook_url="",
                prepare_host_root="/srv/media-stack",
                ingress_name="media-stack-ingress",
                bootstrap_runner_image="registry.example/bootstrap:latest",
                root_dir=ROOT,
                config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
                skip_qbit_ensure=False,
                skip_sab_ensure=False,
            ).timeout_seconds,
            7200,
        )

    def test_manifest_overrides_replaces_namespace_image_and_host_root(self):
        cfg = MODULE.RunBootstrapJobConfig(
            namespace="media-stack-dev",
            timeout_raw="10m",
            heartbeat_interval=15,
            job_log_tail_lines=120,
            alert_webhook_url="",
            prepare_host_root="/mnt/media-dev",
            ingress_name="media-stack-ingress",
            bootstrap_runner_image="registry.example/custom/bootstrap:dev",
            root_dir=ROOT,
            config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
            skip_qbit_ensure=False,
            skip_sab_ensure=False,
        )
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        rendered = runner.manifest_overrides(
            "namespace: media-stack\n"
            "name: media-stack\n"
            "image: 192.168.1.60:30002/library/media-stack-bootstrap-runner:latest\n"
            "path: /srv/media-stack\n"
        )
        self.assertIn("namespace: media-stack-dev", rendered)
        self.assertIn("name: media-stack-dev", rendered)
        self.assertIn("image: registry.example/custom/bootstrap:dev", rendered)
        self.assertIn("/mnt/media-dev", rendered)

    def test_prime_jellyseerr_and_tautulli_keys_into_secret(self):
        cfg = MODULE.RunBootstrapJobConfig(
            namespace="media-stack",
            timeout_raw="10m",
            heartbeat_interval=15,
            job_log_tail_lines=120,
            alert_webhook_url="",
            prepare_host_root="/srv/media-stack",
            ingress_name="media-stack-ingress",
            bootstrap_runner_image="registry.example/bootstrap:latest",
            root_dir=ROOT,
            config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
            skip_qbit_ensure=False,
            skip_sab_ensure=False,
        )
        kube = _RecordingKube()
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=kube,
            tracker=MODULE.PhaseTracker(),
        )

        runner.prime_jellyseerr_api_key_secret()
        runner.prime_tautulli_api_key_secret()

        patch_payloads = []
        for call in kube.calls:
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]:
                patch_payloads.append(json.loads(call[-1]))
        self.assertIn({"stringData": {"JELLYSEERR_API_KEY": "jellyseerr-key"}}, patch_payloads)
        self.assertIn({"stringData": {"TAUTULLI_API_KEY": "tautulli-key"}}, patch_payloads)


if __name__ == "__main__":
    unittest.main()
