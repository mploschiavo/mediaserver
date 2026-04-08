import unittest
import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.controller_core_phases_service import (
    ControllerCorePhasesConfig,
    ControllerCorePhasesService,
)
from media_stack.core.exceptions import ConfigError


class ControllerCorePhasesServiceTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return {
            "config_version": 2,
            "technology_bindings": {
                "media_server": "jellyfin",
                "torrent_client": "qbittorrent",
                "usenet_client": "sabnzbd",
                "indexer_manager": "prowlarr",
            },
            "download_clients": {
                "qbittorrent": {
                    "url": "http://qbittorrent:8080",
                    "configure_arr_clients": True,
                    "set_categories": True,
                },
                "sabnzbd": {
                    "url": "http://sabnzbd:8080",
                    "configure_arr_clients": True,
                },
            },
            "prowlarr_url": "http://prowlarr:9696",
            "arr_apps": [],
            "adapter_hooks": {
                "scale_policy": {
                    "apps": ["jellyfin", "sonarr", "radarr", "prowlarr"],
                },
                "runner_phase_scripts": {
                    "torrent_client_credentials": {
                        "qbittorrent": "ensure-qbit-credentials.sh",
                    },
                    "usenet_client_api_access": {
                        "sabnzbd": "ensure-sabnzbd-api-access.sh",
                    },
                },
                "bootstrap_job": {
                    "runtime_config_policy_handler": (
                        "media_stack.services.apps.stack.controller_config_policy:"
                        "apply_bootstrap_runtime_policy"
                    ),
                    "phase_plan": [
                        {
                            "operation": "run_component_script",
                            "phase_name": "Ensure torrent client bootstrap access ({component|unbound})",
                            "skip_flag": "skip_torrent_client_ensure",
                            "when": {
                                "all_of": [
                                    {
                                        "any_of": [
                                            {"var": "components.torrent_client.selected.configure_arr_clients", "truthy": True},
                                            {"var": "components.torrent_client.selected.set_categories", "truthy": True},
                                        ]
                                    },
                                    {"var": "components.torrent_client.scripts.torrent_client_credentials", "truthy": True},
                                ]
                            },
                            "params": {
                                "component": "torrent_client",
                                "binding": "torrent_client",
                                "script_phase": "torrent_client_credentials",
                                "env": {"NAMESPACE": "$namespace", "PREPARE_HOST_ROOT": "$prepare_host_root"},
                            },
                        },
                        {
                            "operation": "run_component_script",
                            "phase_name": "Ensure usenet client API access ({component|unbound})",
                            "skip_flag": "skip_usenet_client_ensure",
                            "when": {
                                "all_of": [
                                    {"var": "components.usenet_client.selected.configure_arr_clients", "truthy": True},
                                    {"var": "components.usenet_client.scripts.usenet_client_api_access", "truthy": True},
                                ]
                            },
                            "params": {
                                "component": "usenet_client",
                                "binding": "usenet_client",
                                "script_phase": "usenet_client_api_access",
                                "env": {"NAMESPACE": "$namespace"},
                            },
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Prepare bootstrap job config",
                            "params": {"handler": "prepare_bootstrap_job_config"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Ensure bootstrap PVC prerequisites",
                            "params": {"handler": "ensure_bootstrap_pvc_prereqs"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Prime Arr API keys into secret",
                            "params": {"handler": "prime_servarr_api_keys_secret"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Prime usenet client API key into secret ({component|unbound})",
                            "when": {"var": "bindings.usenet_client", "in": ["sabnzbd"]},
                            "params": {
                                "handler": "prime_usenet_client_api_key_secret",
                                "component": "usenet_client",
                                "binding": "usenet_client",
                            },
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Prime request manager API key into secret ({component|unbound})",
                            "when": {"var": "bindings.request_manager", "in": ["jellyseerr"]},
                            "params": {
                                "handler": "prime_request_manager_api_key_secret",
                                "component": "request_manager",
                                "binding": "request_manager",
                            },
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Prime analytics API key into secret",
                            "params": {"handler": "prime_analytics_api_key_secret"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Update bootstrap ConfigMaps",
                            "params": {"handler": "update_bootstrap_configmaps"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Ensure bootstrap Deployment",
                            "params": {"handler": "ensure_bootstrap_deployment"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Wait for bootstrap service",
                            "params": {"handler": "wait_for_bootstrap_service"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Prime media server API key into secret ({component|unbound})",
                            "when": {"var": "bindings.media_server", "in": ["jellyfin"]},
                            "params": {
                                "handler": "prime_media_server_api_key_secret",
                                "component": "media_server",
                                "binding": "media_server",
                            },
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Prime media server user id into secret ({component|unbound})",
                            "when": {"var": "bindings.media_server", "in": ["jellyfin"]},
                            "params": {
                                "handler": "prime_media_server_user_id_secret",
                                "component": "media_server",
                                "binding": "media_server",
                            },
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Print bootstrap Job logs",
                            "params": {"handler": "print_bootstrap_job_logs"},
                        },
                        {
                            "operation": "call_handler",
                            "phase_name": "Activate media server plugins ({component|unbound})",
                            "when": {
                                "all_of": [
                                    {"var": "config.adapter_hooks.bootstrap_job.call_handlers.activate_media_server_plugins", "truthy": True},
                                    {"var": "bindings.media_server", "in": ["jellyfin"]},
                                ]
                            },
                            "params": {
                                "handler": "activate_media_server_plugins",
                                "component": "media_server",
                                "binding": "media_server",
                            },
                        },
                    ],
                },
            },
        }

    def _write_config(self, payload: dict) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "bootstrap.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_run_executes_expected_phases_and_respects_skip_flags(self):
        cfg = self._base_config()
        config_file = self._write_config(cfg)
        svc = ControllerCorePhasesService(
            ControllerCorePhasesConfig(
                config_file=config_file,
                namespace="media-stack",
                prepare_host_root="/srv/media-stack",
                phase_skip_flags={"skip_torrent_client_ensure": True},
            )
        )
        phases: list[tuple[str, bool]] = []

        def _run_phase(name, fn, *, enabled=True):
            phases.append((name, enabled))
            if enabled:
                fn()

        called = {"scripts": []}

        def _run_script(script_name, *args, env=None):
            called["scripts"].append((script_name, tuple(args), env or {}))

        def noop():
            return None

        svc.run(
            run_phase=_run_phase,
            run_script=_run_script,
            operation_handlers={
                "prepare_bootstrap_job_config": noop,
                "ensure_bootstrap_pvc_prereqs": noop,
                "prime_servarr_api_keys_secret": noop,
                "prime_usenet_client_api_key_secret": noop,
                "prime_request_manager_api_key_secret": noop,
                "prime_analytics_api_key_secret": noop,
                "prime_media_server_api_key_secret": noop,
                "prime_media_server_user_id_secret": noop,
                "update_bootstrap_configmaps": noop,
                "ensure_bootstrap_deployment": noop,
                "wait_for_bootstrap_service": noop,
                "print_bootstrap_job_logs": noop,
                "activate_media_server_plugins": noop,
            },
        )

        self.assertEqual(
            phases[0],
            ("Ensure torrent client bootstrap access (qbittorrent)", False),
        )
        self.assertEqual(
            phases[1],
            ("Ensure usenet client API access (sabnzbd)", True),
        )
        self.assertTrue(
            any(
                s[0] in (
                    "ensure-sabnzbd-api-access.sh",
                    "media_stack.services.apps.sabnzbd.cli.ensure_sabnzbd_api_access_main",
                )
                for s in called["scripts"]
            ),
            f"Expected sabnzbd api access script in called scripts: {called['scripts']}",
        )

    def test_run_component_script_requires_non_empty_phase_name(self):
        cfg = self._base_config()
        steps = ((cfg.get("adapter_hooks") or {}).get("bootstrap_job") or {}).get("phase_plan")
        self.assertIsInstance(steps, list)
        self.assertGreater(len(steps), 0)
        steps[0]["phase_name"] = ""

        svc = ControllerCorePhasesService(
            ControllerCorePhasesConfig(
                config_file=self._write_config(cfg),
                namespace="media-stack",
                prepare_host_root="/srv/media-stack",
                phase_skip_flags={},
            )
        )

        with self.assertRaises(ConfigError):
            svc.run(
                run_phase=lambda *_args, **_kwargs: None,
                run_script=lambda *_args, **_kwargs: None,
                operation_handlers={},
            )

    def test_enabled_component_script_requires_resolved_script_mapping(self):
        cfg = self._base_config()
        # Use a fake technology that has no per-service YAML phase_scripts,
        # so the only source for script mappings is the config itself.
        cfg["technology_bindings"]["torrent_client"] = "fake_torrent_client"
        cfg["download_clients"]["fake_torrent_client"] = {"url": "http://fake:8080"}
        hooks = cfg.get("adapter_hooks") or {}
        runner_phase_scripts = hooks.get("runner_phase_scripts") or {}
        runner_phase_scripts["torrent_client_credentials"] = {}
        bootstrap_job = hooks.get("bootstrap_job") or {}
        phase_plan = bootstrap_job.get("phase_plan") or []
        self.assertIsInstance(phase_plan, list)
        self.assertGreater(len(phase_plan), 0)
        phase_plan[0]["when"] = None
        hooks["runner_phase_scripts"] = runner_phase_scripts
        bootstrap_job["phase_plan"] = phase_plan
        hooks["bootstrap_job"] = bootstrap_job
        cfg["adapter_hooks"] = hooks

        svc = ControllerCorePhasesService(
            ControllerCorePhasesConfig(
                config_file=self._write_config(cfg),
                namespace="media-stack",
                prepare_host_root="/srv/media-stack",
                phase_skip_flags={},
            )
        )

        with self.assertRaises(ConfigError):
            svc.run(
                run_phase=lambda *_args, **_kwargs: None,
                run_script=lambda *_args, **_kwargs: None,
                operation_handlers={},
            )


if __name__ == "__main__":
    unittest.main()
