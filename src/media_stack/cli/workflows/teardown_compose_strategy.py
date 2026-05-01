"""Docker Compose teardown planning strategy."""

from __future__ import annotations

import shutil

from media_stack.cli.workflows.teardown_models import TeardownAction, TeardownRequest
from media_stack.cli.workflows.workflow_interfaces import CommandRunner


class TeardownComposeStrategy:
    """Plans Compose teardown actions."""

    def __init__(self, command_runner: CommandRunner) -> None:
        self.command_runner = command_runner

    def plan(self, request: TeardownRequest) -> tuple[TeardownAction, ...]:
        if not self.has_docker():
            return (
                TeardownAction(
                    kind="refuse",
                    description="docker is not on PATH — skipping compose teardown",
                ),
            )
        compose_prefix = self.docker_compose_args()
        if not compose_prefix:
            return (
                TeardownAction(
                    kind="refuse",
                    description=(
                        "docker is on PATH but neither `docker compose` nor `docker-compose` is — "
                        "skipping compose teardown"
                    ),
                ),
            )
        return (
            TeardownAction(
                kind="compose-down",
                description=f"Stop and remove every compose container ({request.compose_file.name})",
                command=(*compose_prefix, "-f", str(request.compose_file), "down", "--remove-orphans"),
                confirm_text="Stop and remove every compose container?",
            ),
        )

    def has_docker(self) -> bool:
        return shutil.which("docker") is not None

    def docker_compose_args(self) -> tuple[str, ...]:
        if self.has_docker():
            try:
                self.command_runner.run_text(["docker", "compose", "version"])
                return ("docker", "compose")
            except Exception:
                pass
        if shutil.which("docker-compose") is not None:
            return ("docker-compose",)
        return ()
