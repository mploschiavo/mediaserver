import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from cli.bootstrap_core_phases_service import (
    BootstrapCorePhasesConfig,
    BootstrapCorePhasesService,
)


class BootstrapCorePhasesServiceTests(unittest.TestCase):
    def test_run_executes_expected_phases_and_respects_skip_flags(self):
        svc = BootstrapCorePhasesService(
            BootstrapCorePhasesConfig(
                config_file=ROOT / "bootstrap" / "media-stack.bootstrap.json",
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
                "resolve_bootstrap_config": noop,
                "ensure_bootstrap_pvc_prereqs": noop,
                "prime_servarr_api_keys_secret": noop,
                "prime_usenet_client_api_key_secret": noop,
                "prime_request_manager_api_key_secret": noop,
                "prime_tautulli_api_key_secret": noop,
                "update_bootstrap_configmaps": noop,
                "recreate_bootstrap_job": noop,
                "wait_for_bootstrap_job": noop,
                "print_bootstrap_job_logs": noop,
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


if __name__ == "__main__":
    unittest.main()
