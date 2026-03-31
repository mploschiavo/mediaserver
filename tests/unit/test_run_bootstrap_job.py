import importlib.util
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


if __name__ == "__main__":
    unittest.main()
