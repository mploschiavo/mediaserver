import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

SPEC = importlib.util.spec_from_file_location(
    "run_bootstrap_job",
    ROOT / "src" / "media_stack" / "cli" / "commands" / "run_controller_job_main.py",
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
    _MINIMAL_CONFIG = {
        "config_version": 2,
        "technology_bindings": {
            "media_server": "jellyfin",
            "torrent_client": "qbittorrent",
            "usenet_client": "sabnzbd",
            "indexer_manager": "prowlarr",
        },
        "download_clients": {
            "qbittorrent": {"url": "http://qbittorrent:8080"},
            "sabnzbd": {"url": "http://sabnzbd:8080"},
        },
        "prowlarr_url": "http://prowlarr:9696",
        "arr_apps": [],
        "adapter_hooks": {
            "scale_policy": {
                "apps": ["jellyfin", "sonarr", "radarr", "prowlarr"],
            },
            "bootstrap_job": {
                "runtime_config_policy_handler": (
                    "media_stack.services.apps.stack.controller_config_policy:"
                    "apply_bootstrap_runtime_policy"
                ),
                "secret_priming_targets": {
                    "request_manager": {
                        "env_key": "JELLYSEERR_API_KEY",
                        "env_var": "JELLYSEERR_API_KEY",
                        "deployment": "jellyseerr",
                        "extract_command": "echo test",
                    },
                    "analytics": {
                        "env_key": "TAUTULLI_API_KEY",
                        "env_var": "TAUTULLI_API_KEY",
                        "deployment": "tautulli",
                        "extract_command": "echo test",
                    },
                },
                "phase_plan": [
                    {
                        "operation": "call_handler",
                        "phase_name": "Prepare bootstrap job config",
                        "params": {"handler": "prepare_bootstrap_job_config"},
                    },
                ],
            },
        },
        "arr_discovery_lists": {
            "trigger_initial_sync": False,
            "Radarr": [{"enable_auto": False, "search_on_add": False}],
            "Lidarr": [{"enable_automatic_add": False, "should_search": False}],
        },
        "sonarr_seed_series": {"enabled": False, "search_for_missing_episodes": False},
        "jellyseerr": {
            "radarr": {"prevent_search": True},
            "sonarr": {"prevent_search": True},
        },
    }

    def _write_temp_config(self, overrides: dict | None = None) -> Path:
        cfg = json.loads(json.dumps(self._MINIMAL_CONFIG))
        if overrides:
            cfg.update(overrides)
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "media-stack.config.json"
        path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return path

    def test_timeout_seconds_parsing(self):
        config_file = self._write_temp_config()
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
                config_file=config_file,
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
                config_file=config_file,
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
                config_file=config_file,
            ).timeout_seconds,
            7200,
        )

    def test_manifest_overrides_replaces_namespace_image_and_host_root(self):
        config_file = self._write_temp_config()
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
            config_file=config_file,
        )
        runner = MODULE.RunBootstrapJobRunner(
            cfg=cfg,
            kube=_FakeKube(),
            tracker=MODULE.PhaseTracker(),
        )

        rendered = runner.manifest_overrides(
            "namespace: media-stack\n"
            "name: media-stack\n"
            "image: harbor.iomio.io/library/media-stack-controller:latest\n"
            "path: /srv/media-stack\n"
        )
        self.assertIn("namespace: media-stack-dev", rendered)
        self.assertIn("name: media-stack-dev", rendered)
        self.assertIn("image: registry.example/custom/bootstrap:dev", rendered)
        self.assertIn("/mnt/media-dev", rendered)

    def test_prime_jellyseerr_and_tautulli_keys_into_secret(self):
        config_file = self._write_temp_config()
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
        config_file = self._write_temp_config()
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

        runner.prepare_bootstrap_job_config()

        written = json.loads(runner.artifacts.job_config_file.read_text(encoding="utf-8"))
        self.assertEqual(written.get("config_version"), 2)
        self.assertIsInstance(written.get("adapter_hooks"), dict)

    def test_prepare_bootstrap_job_config_disables_auto_download_for_manual_mode(self):
        config_file = self._write_temp_config()
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
        config_file = self._write_temp_config()
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
