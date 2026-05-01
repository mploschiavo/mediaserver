"""Execute teardown plans after safety and confirmation checks."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass

from media_stack.cli.workflows.teardown_filesystem_service import TeardownFileSystemService
from media_stack.cli.workflows.teardown_models import (
    TEARDOWN_ACTION_KILL_PID,
    TEARDOWN_ACTION_REFUSE,
    TEARDOWN_ACTION_RM_TREE,
    TEARDOWN_SCOPE_EVERYTHING,
    TEARDOWN_TARGET_BOTH,
    TEARDOWN_TARGET_COMPOSE,
    TEARDOWN_TARGET_K8S,
    TeardownAction,
    TeardownPlan,
    TeardownResult,
)
from media_stack.cli.workflows.workflow_confirmation_service import InteractiveConfirmationPolicy
from media_stack.cli.workflows.workflow_interfaces import CommandRunner, ConfirmationPolicy, FileSystemGateway, Notifier
from media_stack.cli.workflows.workflow_notification_service import WorkflowNotificationService

TEARDOWN_ACTION_COMPOSE_DOWN = "compose-down"
TEARDOWN_ACTION_K8S_DELETE_NS = "k8s-delete-ns"
WINDOWS_PLATFORM_NAME = "Windows"


@dataclass
class TeardownExecutorService:
    """Executes a precomputed teardown plan."""

    command_runner: CommandRunner
    confirmation_policy: ConfirmationPolicy
    notifier: Notifier
    filesystem: FileSystemGateway

    def execute(self, plan: TeardownPlan) -> TeardownResult:
        self.print_banner(plan)
        if not plan.actions:
            self.notifier.info("Nothing to do: no docker/kubectl target or local state to wipe.")
            return TeardownResult(plan=plan)
        self.print_plan(plan)
        if plan.dry_run:
            self.notifier.info("Dry-run complete: no changes made.")
            return TeardownResult(plan=plan)
        failures = 0
        for action in plan.actions:
            if not self.confirm(action):
                self.notifier.info(f"Skipped: {action.describe()}")
                continue
            try:
                self.run_action(action)
            except Exception as exc:
                self.notifier.error(f"{action.describe()}: {exc}")
                failures += 1
        if failures:
            self.notifier.warn(f"Teardown finished with {failures} failure(s).")
            return TeardownResult(plan=plan, failures=failures)
        self.notifier.info("Teardown complete.")
        self.print_next_steps(plan)
        return TeardownResult(plan=plan)

    def confirm(self, action: TeardownAction) -> bool:
        if action.confirm_text is None:
            return True
        return self.confirmation_policy.approve(
            action.confirm_text,
            requires_double_confirm=action.requires_double_confirm,
        )

    def run_action(self, action: TeardownAction) -> None:
        if action.kind == TEARDOWN_ACTION_REFUSE:
            self.notifier.info(action.describe())
            return
        if action.kind in (TEARDOWN_ACTION_COMPOSE_DOWN, TEARDOWN_ACTION_K8S_DELETE_NS):
            self.notifier.info(f"Run: {' '.join(action.command)}")
            self.command_runner.run_text(action.command, check=False)
            return
        if action.kind == TEARDOWN_ACTION_KILL_PID:
            if action.pid is None:
                return
            self.notifier.info(f"Kill stale process pid {action.pid}")
            if platform.system() == WINDOWS_PLATFORM_NAME:
                self.command_runner.run_text(["taskkill", "/PID", str(action.pid), "/F"], check=False)
            else:
                os.kill(action.pid, 15)
            return
        if action.kind == TEARDOWN_ACTION_RM_TREE and action.path is not None:
            self.notifier.info(f"Remove: {action.path}")
            self.filesystem.remove_tree(action.path)
            return
        raise AssertionError(f"unknown action kind: {action.kind}")

    def print_banner(self, plan: TeardownPlan) -> None:
        self.notifier.info("Media-stack teardown")
        self.notifier.info(f"Target: {plan.target}")
        self.notifier.info(f"Scope: {plan.scope}")
        self.notifier.info(f"Environment: {plan.environment}")
        self.notifier.info(f"Compose file: {plan.compose_file}")
        self.notifier.info(f"CONFIG_ROOT: {plan.config_root}")
        self.notifier.info(f"DATA_ROOT: {plan.data_root}")
        if plan.scope == TEARDOWN_SCOPE_EVERYTHING:
            self.notifier.info(f"MEDIA_ROOT: {plan.media_root}")
        if plan.target in (TEARDOWN_TARGET_K8S, TEARDOWN_TARGET_BOTH):
            self.notifier.info(f"K8s namespace: {plan.k8s_namespace}")
        if plan.dry_run:
            self.notifier.info("Mode: DRY-RUN / PREVIEW")

    def print_plan(self, plan: TeardownPlan) -> None:
        self.notifier.info("Planned actions in order:")
        for index, action in enumerate(plan.actions, 1):
            self.notifier.info(f"{index}. {action.describe()}")

    def print_next_steps(self, plan: TeardownPlan) -> None:
        self.notifier.info("Next steps:")
        if plan.target in (TEARDOWN_TARGET_COMPOSE, TEARDOWN_TARGET_BOTH):
            self.notifier.info(f"docker compose -f {plan.compose_file} up -d")
        if plan.target in (TEARDOWN_TARGET_K8S, TEARDOWN_TARGET_BOTH):
            self.notifier.info("media-stack-deploy")


class TeardownExecutorFactory:
    """Builds the default executor for CLI use."""

    def create(self, command_runner: CommandRunner, *, assume_yes: bool) -> TeardownExecutorService:
        return TeardownExecutorService(
            command_runner=command_runner,
            confirmation_policy=InteractiveConfirmationPolicy(assume_yes=assume_yes),
            notifier=WorkflowNotificationService(),
            filesystem=TeardownFileSystemService(),
        )
