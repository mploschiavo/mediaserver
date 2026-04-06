import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location(
    "run_bootstrap_job",
    ROOT / "src" / "media_stack" / "cli" / "commands" / "run_bootstrap_job_main.py",
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
                config_file=ROOT / "contracts" / "media-stack.config.json",
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
                config_file=ROOT / "contracts" / "media-stack.config.json",
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
                config_file=ROOT / "contracts" / "media-stack.config.json",
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
            config_file=ROOT / "contracts" / "media-stack.config.json",
        )
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        rendered = runner.manifest_overrides(
            "namespace: media-stack\n"
            "name: media-stack\n"
            "image: 192.168.1.60:30002/library/media-stack-controller:latest\n"
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
            config_file=ROOT / "contracts" / "media-stack.config.json",
        )
        kube = _RecordingKube()
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=kube,
            tracker=MODULE.PhaseTracker(),
        )

        runner.prepare_bootstrap_job_config()
        runner.prime_request_manager_api_key_secret()
        runner.prime_analytics_api_key_secret()

        patch_payloads = []
        for call in kube.calls:
            if call[:5] == ["-n", "media-stack", "patch", "secret", "media-stack-secrets"]:
                patch_payloads.append(json.loads(call[-1]))
        self.assertIn({"stringData": {"JELLYSEERR_API_KEY": "jellyseerr-key"}}, patch_payloads)
        self.assertIn({"stringData": {"TAUTULLI_API_KEY": "tautulli-key"}}, patch_payloads)

    def test_prepare_bootstrap_job_config_writes_validated_json(self):
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
            config_file=ROOT / "contracts" / "media-stack.config.json",
        )
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        runner.prepare_bootstrap_job_config()

        written = json.loads(runner.artifacts.job_config_file.read_text(encoding="utf-8"))
        self.assertEqual(written.get("config_version"), 2)
        self.assertIsInstance(written.get("adapter_hooks"), dict)

    def test_prepare_bootstrap_job_config_disables_auto_download_for_manual_mode(self):
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
            config_file=ROOT / "contracts" / "media-stack.config.json",
            auto_download_content=False,
        )
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        runner.prepare_bootstrap_job_config()
        written = json.loads(runner.artifacts.job_config_file.read_text(encoding="utf-8"))

        discovery = dict(written.get("arr_discovery_lists") or {})
        self.assertFalse(bool(discovery.get("trigger_initial_sync")))
        radarr_lists = discovery.get("Radarr") or []
        lidarr_lists = discovery.get("Lidarr") or []
        if radarr_lists:
            self.assertFalse(bool(radarr_lists[0].get("enable_auto")))
            self.assertFalse(bool(radarr_lists[0].get("search_on_add")))
        if lidarr_lists:
            self.assertFalse(bool(lidarr_lists[0].get("enable_automatic_add")))
            self.assertFalse(bool(lidarr_lists[0].get("should_search")))

        sonarr_seed = dict(written.get("sonarr_seed_series") or {})
        self.assertFalse(bool(sonarr_seed.get("enabled")))
        self.assertFalse(bool(sonarr_seed.get("search_for_missing_episodes")))

        jellyseerr = dict(written.get("jellyseerr") or {})
        self.assertTrue(bool(dict(jellyseerr.get("radarr") or {}).get("prevent_search")))
        self.assertTrue(bool(dict(jellyseerr.get("sonarr") or {}).get("prevent_search")))

    def test_prepare_bootstrap_job_config_enables_auto_download_for_full_mode(self):
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
            config_file=ROOT / "contracts" / "media-stack.config.json",
            auto_download_content=True,
        )
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        runner.prepare_bootstrap_job_config()
        written = json.loads(runner.artifacts.job_config_file.read_text(encoding="utf-8"))

        discovery = dict(written.get("arr_discovery_lists") or {})
        self.assertTrue(bool(discovery.get("trigger_initial_sync")))
        radarr_lists = discovery.get("Radarr") or []
        lidarr_lists = discovery.get("Lidarr") or []
        if radarr_lists:
            self.assertTrue(bool(radarr_lists[0].get("enable_auto")))
            self.assertTrue(bool(radarr_lists[0].get("search_on_add")))
        if lidarr_lists:
            self.assertTrue(bool(lidarr_lists[0].get("enable_automatic_add")))
            self.assertTrue(bool(lidarr_lists[0].get("should_search")))

        sonarr_seed = dict(written.get("sonarr_seed_series") or {})
        self.assertTrue(bool(sonarr_seed.get("enabled")))
        self.assertTrue(bool(sonarr_seed.get("search_for_missing_episodes")))

        jellyseerr = dict(written.get("jellyseerr") or {})
        self.assertFalse(bool(dict(jellyseerr.get("radarr") or {}).get("prevent_search")))
        self.assertFalse(bool(dict(jellyseerr.get("sonarr") or {}).get("prevent_search")))

    def test_prepare_bootstrap_job_config_rejects_non_object_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "bad.json"
            config_file.write_text("[]", encoding="utf-8")
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
                config_file=config_file,
            )
            runner = MODULE.RunBootstrapJobRunner(
                cfg=cfg,
                kube=_FakeKube(),
                tracker=MODULE.PhaseTracker(),
            )
            with self.assertRaises(MODULE.ConfigError):
                runner.prepare_bootstrap_job_config()


if __name__ == "__main__":
    unittest.main()
