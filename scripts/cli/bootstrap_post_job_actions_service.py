from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class BootstrapPostJobAction:
    marker: str
    phase_name: str
    deployment: str
    timeout_seconds: int = 180
    restart_if_exists: bool = True


class BootstrapPostJobActionsService:
    """Apply post-job restart actions when bootstrap logs indicate config writes."""

    def __init__(self, actions: list[BootstrapPostJobAction] | None = None) -> None:
        self._actions = actions or [
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

    def run_actions(
        self,
        *,
        log_contains: Callable[[str], bool],
        run_phase: Callable[[str, Callable[[], None]], None],
        restart_deployment: Callable[[str], None],
        restart_deployment_if_exists: Callable[[str], None],
    ) -> None:
        for action in self._actions:
            if not log_contains(action.marker):
                continue

            if action.restart_if_exists:
                run_phase(
                    action.phase_name,
                    lambda deployment=action.deployment: restart_deployment_if_exists(deployment),
                )
            else:
                run_phase(
                    action.phase_name,
                    lambda deployment=action.deployment: restart_deployment(deployment),
                )
