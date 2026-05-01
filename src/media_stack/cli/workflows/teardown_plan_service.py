"""Build deterministic teardown plans."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from media_stack.cli.workflows.teardown_compose_strategy import TeardownComposeStrategy
from media_stack.cli.workflows.teardown_filesystem_service import TeardownFileSystemService
from media_stack.cli.workflows.teardown_kubernetes_strategy import TeardownKubernetesStrategy
from media_stack.cli.workflows.teardown_models import (
    TEARDOWN_SCOPE_DATA,
    TEARDOWN_SCOPE_EVERYTHING,
    TEARDOWN_TARGET_AUTO,
    TEARDOWN_TARGET_BOTH,
    TEARDOWN_TARGET_COMPOSE,
    TEARDOWN_TARGET_K8S,
    ResolvedTeardownTarget,
    TeardownAction,
    TeardownPlan,
    TeardownRequest,
)
from media_stack.cli.workflows.teardown_safety_policy_service import TeardownSafetyPolicyService
from media_stack.cli.workflows.workflow_interfaces import CommandRunner, FileSystemGateway


class TeardownPlanService:
    """Translates teardown requests into executable plans."""

    compose_host_ports = (8080, 8989, 7878, 6767, 8686, 8787, 9117)
    PROCESS_DISCOVERY_TIMEOUT_SECONDS = 5
    PROCESS_COMMAND_TIMEOUT_SECONDS = 2

    def __init__(
        self,
        command_runner: CommandRunner,
        *,
        compose_strategy: TeardownComposeStrategy | None = None,
        kubernetes_strategy: TeardownKubernetesStrategy | None = None,
        safety_policy: TeardownSafetyPolicyService | None = None,
        filesystem: FileSystemGateway | None = None,
    ) -> None:
        self.command_runner = command_runner
        self.safety_policy = safety_policy or TeardownSafetyPolicyService()
        self.filesystem = filesystem or TeardownFileSystemService()
        self.compose_strategy = compose_strategy or TeardownComposeStrategy(command_runner)
        self.kubernetes_strategy = kubernetes_strategy or TeardownKubernetesStrategy(self.safety_policy)

    def build_plan(self, request: TeardownRequest) -> TeardownPlan:
        denied = self.safety_policy.request_denial_action(request)
        if denied is not None:
            return self.safety_policy.validate_plan(self.empty_plan(request, denied))
        target = self.resolve_target(request.target)
        actions: list[TeardownAction] = []
        if target in (TEARDOWN_TARGET_COMPOSE, TEARDOWN_TARGET_BOTH):
            actions.extend(self.compose_strategy.plan(request))
        if target in (TEARDOWN_TARGET_K8S, TEARDOWN_TARGET_BOTH):
            actions.extend(self.kubernetes_strategy.plan(request))
        actions.extend(self.stale_port_forward_actions())
        actions.extend(self.config_cleanup_actions(request))
        actions.extend(self.data_cleanup_actions(request))
        plan = TeardownPlan(
            target=target,
            scope=request.scope,
            compose_file=request.compose_file,
            config_root=request.config_root,
            data_root=request.data_root,
            media_root=request.media_root,
            k8s_namespace=request.k8s_namespace,
            dry_run=request.dry_run,
            assume_yes=request.assume_yes,
            environment=request.environment,
            confirmation_token=request.confirmation_token,
            actions=tuple(actions),
        )
        return self.safety_policy.validate_plan(plan)

    def empty_plan(self, request: TeardownRequest, *actions: TeardownAction) -> TeardownPlan:
        return TeardownPlan(
            target=self.resolve_target(request.target),
            scope=request.scope,
            compose_file=request.compose_file,
            config_root=request.config_root,
            data_root=request.data_root,
            media_root=request.media_root,
            k8s_namespace=request.k8s_namespace,
            dry_run=request.dry_run,
            assume_yes=request.assume_yes,
            environment=request.environment,
            confirmation_token=request.confirmation_token,
            actions=tuple(actions),
        )

    def resolve_target(self, target: str) -> ResolvedTeardownTarget:
        if target != TEARDOWN_TARGET_AUTO:
            return target  # type: ignore[return-value]
        have_docker = self.compose_strategy.has_docker()
        have_k8s = self.kubernetes_strategy.has_kubectl()
        if have_docker and have_k8s:
            return TEARDOWN_TARGET_BOTH
        if have_k8s:
            return TEARDOWN_TARGET_K8S
        return TEARDOWN_TARGET_COMPOSE

    def config_cleanup_actions(self, request: TeardownRequest) -> list[TeardownAction]:
        actions = []
        for child in self.safety_policy.config_children_to_wipe(request.config_root):
            actions.append(
                TeardownAction(
                    kind="rm-tree",
                    description=f"Delete {child} ({self.human_bytes(self.filesystem.dir_size(child))})",
                    path=child,
                    confirm_text=f"Delete {child}? (config/defaults/ is preserved.)",
                )
            )
        return actions

    def data_cleanup_actions(self, request: TeardownRequest) -> list[TeardownAction]:
        actions = []
        if request.scope in (TEARDOWN_SCOPE_DATA, TEARDOWN_SCOPE_EVERYTHING) and request.data_root.is_dir():
            actions.append(
                TeardownAction(
                    kind="rm-tree",
                    description=(
                        f"Delete {request.data_root} (torrents/usenet/transcode — "
                        f"{self.human_bytes(self.filesystem.dir_size(request.data_root))})"
                    ),
                    path=request.data_root,
                    confirm_text=f"Wipe {request.data_root} (active torrent / usenet state)?",
                )
            )
        if request.scope == TEARDOWN_SCOPE_EVERYTHING and request.media_root.is_dir():
            actions.append(
                TeardownAction(
                    kind="rm-tree",
                    description=(
                        f"Delete {request.media_root} (downloaded films/shows — "
                        f"{self.human_bytes(self.filesystem.dir_size(request.media_root))})"
                    ),
                    path=request.media_root,
                    confirm_text=(
                        f"REALLY wipe {request.media_root}? This deletes downloaded films AND shows."
                    ),
                    requires_double_confirm=True,
                )
            )
        return actions

    def stale_port_forward_actions(self) -> list[TeardownAction]:
        actions: list[TeardownAction] = []
        for port in self.compose_host_ports:
            for pid, command in self.find_pids_listening_on(port):
                if self.is_kubectl_port_forward(command):
                    actions.append(
                        TeardownAction(
                            kind="kill-pid",
                            description=f"Kill stale kubectl port-forward holding :{port} (pid {pid})",
                            pid=pid,
                        )
                    )
        return actions

    def find_pids_listening_on(self, port: int) -> list[tuple[int, str]]:
        if platform.system() == "Windows":
            return self.find_windows_pids_listening_on(port)
        return self.find_posix_pids_listening_on(port)

    def find_windows_pids_listening_on(self, port: int) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                timeout=self.PROCESS_DISCOVERY_TIMEOUT_SECONDS,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return out
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[3].upper() != "LISTENING" or not parts[1].endswith(f":{port}"):
                continue
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            out.append((pid, self.windows_cmdline(pid)))
        return out

    def find_posix_pids_listening_on(self, port: int) -> list[tuple[int, str]]:
        if shutil.which("lsof") is None:
            return []
        out: list[tuple[int, str]] = []
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=self.PROCESS_DISCOVERY_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return out
        for raw in result.stdout.splitlines():
            try:
                pid = int(raw.strip())
            except ValueError:
                continue
            out.append((pid, self.posix_cmdline(pid)))
        return out

    def posix_cmdline(self, pid: int) -> str:
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                return handle.read().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            try:
                result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True,
                    text=True,
                    timeout=self.PROCESS_COMMAND_TIMEOUT_SECONDS,
                    check=False,
                )
                return result.stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return ""

    def windows_cmdline(self, pid: int) -> str:
        try:
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                capture_output=True,
                text=True,
                timeout=self.PROCESS_DISCOVERY_TIMEOUT_SECONDS,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""
        for line in result.stdout.splitlines():
            if line.startswith("CommandLine="):
                return line[len("CommandLine="):].strip()
        return ""

    def is_kubectl_port_forward(self, command: str) -> bool:
        return "kubectl" in command and "port-forward" in command

    def human_bytes(self, value: int) -> str:
        suffixes = ("B", "KiB", "MiB", "GiB", "TiB")
        current = float(value)
        index = 0
        while current >= 1024 and index < len(suffixes) - 1:
            current /= 1024
            index += 1
        return f"{current:.1f} {suffixes[index]}"
