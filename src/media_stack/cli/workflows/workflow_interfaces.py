"""Shared workflow protocols for release, deploy, and teardown orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, TypeVar

TRequest = TypeVar("TRequest")
TResult = TypeVar("TResult")
TPlan = TypeVar("TPlan")


class CommandRunner(Protocol):
    """Runs external commands behind a testable boundary."""

    def run_text(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> str:
        """Run a command and return stdout text."""

    def run_json(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> Any:
        """Run a command and parse stdout as JSON."""


class Notifier(Protocol):
    """Workflow progress notifications."""

    def info(self, message: str) -> None:
        """Emit informational progress."""

    def warn(self, message: str) -> None:
        """Emit a warning."""

    def error(self, message: str) -> None:
        """Emit an error."""


class ConfirmationPolicy(Protocol):
    """Approves or denies destructive workflow actions."""

    def approve(self, prompt: str, *, requires_double_confirm: bool = False) -> bool:
        """Return whether the requested destructive action is approved."""


class ImageBuilder(Protocol[TRequest, TResult]):
    """Builds and optionally publishes images."""

    def build(self, request: TRequest) -> TResult:
        """Build images for a request."""


class DeploymentStrategy(Protocol[TRequest, TResult]):
    """Deploys and verifies a target runtime."""

    def deploy(self, request: TRequest) -> TResult:
        """Deploy to the runtime."""

    def verify(self, request: TRequest) -> TResult:
        """Verify runtime state."""


class VerificationStrategy(Protocol[TRequest, TResult]):
    """Observes a deployed runtime without mutating it."""

    def verify(self, request: TRequest) -> TResult:
        """Return a proof object for the observed runtime state."""


class TeardownStrategy(Protocol[TRequest, TPlan]):
    """Plans teardown actions for one runtime target."""

    def plan(self, request: TRequest) -> Sequence[TPlan]:
        """Return teardown actions for this strategy."""


class SecretProvider(Protocol):
    """Reads/writes secret material for workflows that need it."""

    def read(self, name: str) -> str:
        """Read a secret by logical name."""

    def write(self, name: str, value: str) -> None:
        """Write a secret by logical name."""


class FileSystemGateway(Protocol):
    """Filesystem operations used by destructive workflows."""

    def dir_size(self, path: Path) -> int:
        """Return best-effort recursive byte size."""

    def remove_tree(self, path: Path) -> None:
        """Remove a file, symlink, or directory tree."""
