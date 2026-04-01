import unittest

from scripts.cli.bootstrap_post_job_actions_service import (
    BootstrapPostJobAction,
    BootstrapPostJobActionsService,
)


class BootstrapPostJobActionsServiceTests(unittest.TestCase):
    def test_runs_only_matching_actions(self):
        service = BootstrapPostJobActionsService(
            actions=[
                BootstrapPostJobAction(
                    marker="Jellyseerr: settings file bootstrap applied",
                    phase_name="Restart Jellyseerr after file bootstrap",
                    deployment="jellyseerr",
                    restart_if_exists=False,
                ),
                BootstrapPostJobAction(
                    marker="Homepage: wrote services config",
                    phase_name="Restart Homepage after config sync",
                    deployment="homepage",
                    restart_if_exists=True,
                ),
                BootstrapPostJobAction(
                    marker="Bazarr: wrote integration config",
                    phase_name="Restart Bazarr after config sync",
                    deployment="bazarr",
                    restart_if_exists=True,
                ),
            ]
        )
        phases: list[str] = []
        restarts: list[str] = []
        restarts_if_exists: list[str] = []
        markers = {
            "Jellyseerr: settings file bootstrap applied",
            "Bazarr: wrote integration config",
        }

        def _run_phase(name, fn):
            phases.append(name)
            fn()

        service.run_actions(
            log_contains=lambda marker: marker in markers,
            run_phase=_run_phase,
            restart_deployment=lambda deployment: restarts.append(deployment),
            restart_deployment_if_exists=lambda deployment: restarts_if_exists.append(deployment),
        )

        self.assertEqual(
            phases,
            [
                "Restart Jellyseerr after file bootstrap",
                "Restart Bazarr after config sync",
            ],
        )
        self.assertEqual(restarts, ["jellyseerr"])
        self.assertEqual(restarts_if_exists, ["bazarr"])


if __name__ == "__main__":
    unittest.main()
