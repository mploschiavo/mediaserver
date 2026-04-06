import unittest
import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from media_stack.cli.workflows.bootstrap_core_phases_service import (
    BootstrapCorePhasesConfig,
    BootstrapCorePhasesService,
)
from media_stack.core.exceptions import ConfigError


class BootstrapCorePhasesServiceTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return json.loads(
            (ROOT / "contracts" / "media-stack.config.json").read_text(encoding="utf-8")
        )

    def _write_config(self, payload: dict) -> Path:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / "bootstrap.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_run_executes_expected_phases_and_respects_skip_flags(self):
        svc = BootstrapCorePhasesService(
            BootstrapCorePhasesConfig(
                config_file=ROOT / "contracts" / "media-stack.config.json",
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
        self.assertTrue(any(s[0] == "ensure-sabnzbd-api-access.sh" for s in called["scripts"]))

    def test_run_component_script_requires_non_empty_phase_name(self):
        cfg = self._base_config()
        steps = ((cfg.get("adapter_hooks") or {}).get("bootstrap_job") or {}).get("phase_plan")
        self.assertIsInstance(steps, list)
        self.assertGreater(len(steps), 0)
        steps[0]["phase_name"] = ""

        svc = BootstrapCorePhasesService(
            BootstrapCorePhasesConfig(
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

        svc = BootstrapCorePhasesService(
            BootstrapCorePhasesConfig(
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
