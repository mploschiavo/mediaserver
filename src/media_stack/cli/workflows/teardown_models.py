"""Domain models for destructive teardown workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

TeardownTarget = Literal["auto", "compose", "k8s", "both"]
ResolvedTeardownTarget = Literal["compose", "k8s", "both"]
TeardownScope = Literal["config", "data", "everything"]
TeardownEnvironment = Literal["local", "dev", "staging", "prod"]
TeardownActionKind = Literal["compose-down", "k8s-delete-ns", "rm-tree", "kill-pid", "refuse"]

TEARDOWN_TARGET_AUTO = "auto"
TEARDOWN_TARGET_COMPOSE = "compose"
TEARDOWN_TARGET_K8S = "k8s"
TEARDOWN_TARGET_BOTH = "both"
TEARDOWN_SCOPE_DATA = "data"
TEARDOWN_SCOPE_EVERYTHING = "everything"
TEARDOWN_ENV_PROD = "prod"
TEARDOWN_ACTION_REFUSE = "refuse"
TEARDOWN_ACTION_RM_TREE = "rm-tree"
TEARDOWN_ACTION_KILL_PID = "kill-pid"
PROD_CONFIRMATION_PREFIX = "TEARDOWN"


@dataclass(frozen=True)
class TeardownRequest:
    """Operator request for a teardown plan or execution."""

    target: TeardownTarget
    scope: TeardownScope
    compose_file: Path
    config_root: Path
    data_root: Path
    media_root: Path
    k8s_namespace: str
    dry_run: bool = True
    assume_yes: bool = False
    environment: TeardownEnvironment = "local"
    confirmation_token: str = ""


@dataclass(frozen=True)
class TeardownAction:
    """Single planned teardown operation."""

    kind: TeardownActionKind
    description: str
    path: Path | None = None
    command: tuple[str, ...] = ()
    pid: int | None = None
    confirm_text: str | None = None
    requires_double_confirm: bool = False

    def describe(self) -> str:
        return self.description


@dataclass(frozen=True)
class TeardownPlan:
    """Deterministic teardown plan generated before execution."""

    target: ResolvedTeardownTarget
    scope: TeardownScope
    compose_file: Path
    config_root: Path
    data_root: Path
    media_root: Path
    k8s_namespace: str
    dry_run: bool
    assume_yes: bool
    environment: TeardownEnvironment
    confirmation_token: str = ""
    actions: tuple[TeardownAction, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TeardownResult:
    """Execution outcome for a teardown plan."""

    plan: TeardownPlan
    failures: int = 0

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0
