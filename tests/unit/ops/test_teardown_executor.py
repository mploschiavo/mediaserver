"""Tests for safe teardown execution behavior."""

from __future__ import annotations

from pathlib import Path

from media_stack.cli.workflows.teardown_executor_service import TeardownExecutorService
from media_stack.cli.workflows.teardown_models import TeardownAction, TeardownPlan


class FakeCommandRunner:
    """Records commands without touching the host."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run_text(self, command, *, env=None, check=True):
        self.commands.append(tuple(command))
        return ""

    def run_json(self, command, *, env=None, check=True):
        self.commands.append(tuple(command))
        return {}


class FakeConfirmationPolicy:
    """Configurable confirmation test double."""

    def __init__(self, approved: bool = True) -> None:
        self.approved = approved
        self.prompts: list[str] = []

    def approve(self, prompt: str, *, requires_double_confirm: bool = False) -> bool:
        self.prompts.append(prompt)
        return self.approved


class FakeNotifier:
    """Captures workflow output."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)

    def warn(self, message: str) -> None:
        self.messages.append(message)

    def error(self, message: str) -> None:
        self.messages.append(message)


class FakeFileSystem:
    """Records removals without deleting files."""

    def __init__(self) -> None:
        self.removed: list[Path] = []

    def dir_size(self, path: Path) -> int:
        return 0

    def remove_tree(self, path: Path) -> None:
        self.removed.append(path)


class TeardownExecutorFixture:
    """Builds a safe executor fixture."""

    def __init__(self, *, approved: bool = True) -> None:
        self.runner = FakeCommandRunner()
        self.confirmation = FakeConfirmationPolicy(approved=approved)
        self.notifier = FakeNotifier()
        self.filesystem = FakeFileSystem()
        self.executor = TeardownExecutorService(
            command_runner=self.runner,
            confirmation_policy=self.confirmation,
            notifier=self.notifier,
            filesystem=self.filesystem,
        )


def _plan(*, dry_run: bool, actions: tuple[TeardownAction, ...]) -> TeardownPlan:
    root = Path("/tmp/media-stack")
    return TeardownPlan(
        target="compose",
        scope="config",
        compose_file=root / "docker-compose.yml",
        config_root=root / "config",
        data_root=root / "data",
        media_root=root / "media",
        k8s_namespace="media-stack",
        dry_run=dry_run,
        assume_yes=False,
        environment="local",
        actions=actions,
    )


class TestTeardownExecutorService:
    """Execution safety gates keep preview and denied actions non-mutating."""

    def test_dry_run_never_executes_commands_or_removes_files(self) -> None:
        fixture = TeardownExecutorFixture()
        plan = _plan(
            dry_run=True,
            actions=(
                TeardownAction(
                    kind="compose-down",
                    description="compose down",
                    command=("docker", "compose", "down"),
                ),
                TeardownAction(kind="rm-tree", description="remove config", path=Path("/tmp/config")),
            ),
        )
        result = fixture.executor.execute(plan)
        assert result.exit_code == 0
        assert fixture.runner.commands == []
        assert fixture.filesystem.removed == []

    def test_execute_runs_compose_command_after_confirmation(self) -> None:
        fixture = TeardownExecutorFixture()
        action = TeardownAction(
            kind="compose-down",
            description="compose down",
            command=("docker", "compose", "down"),
            confirm_text="Stop compose?",
        )
        result = fixture.executor.execute(_plan(dry_run=False, actions=(action,)))
        assert result.exit_code == 0
        assert fixture.runner.commands == [("docker", "compose", "down")]
        assert fixture.confirmation.prompts == ["Stop compose?"]

    def test_denied_confirmation_does_not_execute(self) -> None:
        fixture = TeardownExecutorFixture(approved=False)
        action = TeardownAction(
            kind="k8s-delete-ns",
            description="delete namespace",
            command=("kubectl", "delete", "namespace", "media-stack"),
            confirm_text="Delete namespace?",
        )
        result = fixture.executor.execute(_plan(dry_run=False, actions=(action,)))
        assert result.exit_code == 0
        assert fixture.runner.commands == []
