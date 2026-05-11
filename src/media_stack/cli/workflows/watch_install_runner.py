"""WatchInstallRunner — live install/bootstrap watcher for media-stack.

ADR-0015 Phase 7h. Pre-Phase-7h the entire watcher (per-tick
snapshot + pod-readiness summary + deployment-pending summary +
event filter + bootstrap-job tail) lived inline as 10 methods
on ``WatchInstallCommand`` in commands/. Phase 7h moves the
workflow logic onto this class; the commands shim shrinks to
argparse + main.
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from dataclasses import dataclass
from typing import Callable

from media_stack.core.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import ConfigError
from media_stack.core.logging_utils import log_swallowed


_UNHEALTHY_POD_STATUSES = frozenset(
    {"CrashLoopBackOff", "Error", "ImagePullBackOff", "RunContainerError"}
)
_WARNING_EVENT_TOKENS = ("Warning", "Failed", "BackOff", "Unhealthy", "Error")
_BOOTSTRAP_POD_SELECTOR = "app=media-stack-controller"
_RECENT_JOB_TAIL_COUNT = 5


@dataclass(frozen=True)
class WatchInstallConfig:
    namespace: str
    interval_seconds: int
    event_lines: int
    job_log_lines: int
    once: bool


class WatchInstallRunner:
    """Workflow runner: per-tick snapshot of install / bootstrap state."""

    def ts(self) -> str:
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

    def info(self, message: str) -> None:
        print(f"[{self.ts()}] [INFO] {message}")

    def warn(self, message: str) -> None:
        print(f"[{self.ts()}] [WARN] {message}", file=sys.stderr)

    def print_command_output(self, cmd: list[str]) -> str:
        proc = run_command(cmd, check=False)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if stdout:
            sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        return stdout

    def pod_readiness_summary(self, pods_table: str) -> tuple[int, int]:
        not_ready = 0
        unhealthy = 0
        for line in pods_table.splitlines():
            row = line.strip()
            if not row:
                continue
            parts = row.split()
            if len(parts) < 3:
                continue
            readiness = parts[1]
            status = parts[2]
            if "/" in readiness:
                try:
                    ready, total = readiness.split("/", 1)
                    if int(ready) != int(total):
                        not_ready += 1
                except (ValueError, IndexError) as exc:
                    log_swallowed(exc)
            if status in _UNHEALTHY_POD_STATUSES:
                unhealthy += 1
        return not_ready, unhealthy

    def deployment_pending_summary(self, deploy_table: str) -> int:
        pending = 0
        for line in deploy_table.splitlines():
            row = line.strip()
            if not row:
                continue
            parts = row.split()
            if len(parts) < 3:
                continue
            try:
                ready = int(parts[1])
                desired = int(parts[2])
                if ready != desired:
                    pending += 1
            except (ValueError, IndexError) as exc:
                log_swallowed(exc)
                continue
        return pending

    def snapshot(self, kubectl: list[str], cfg: WatchInstallConfig) -> None:
        print("\n==================================================================")
        self.info("Install Watch Snapshot")
        self.info(f"Namespace: {cfg.namespace} | Refresh: {cfg.interval_seconds}s")

        self._snapshot_pods(kubectl, cfg)
        self._snapshot_deployments(kubectl, cfg)
        self._snapshot_events(kubectl, cfg)
        self._snapshot_jobs_and_logs(kubectl, cfg)

    def _snapshot_pods(self, kubectl: list[str], cfg: WatchInstallConfig) -> None:
        self.info("Pod status")
        pods_stdout = self.print_command_output(
            [*kubectl, "-n", cfg.namespace, "get", "pods"],
        )
        pods_no_header = (
            "\n".join(pods_stdout.splitlines()[1:]) if pods_stdout else ""
        )
        if pods_no_header.strip():
            not_ready, unhealthy = self.pod_readiness_summary(pods_no_header)
            self.info(
                f"Pod readiness summary: not_ready={not_ready}, unhealthy={unhealthy}"
            )
        else:
            self.warn(f"No pods found in namespace {cfg.namespace}")

    def _snapshot_deployments(
        self, kubectl: list[str], cfg: WatchInstallConfig,
    ) -> None:
        self.info("Deployment rollout status")
        deploy_stdout = self.print_command_output(
            [*kubectl, "-n", cfg.namespace, "get", "deploy"],
        )
        deploy_no_header = (
            "\n".join(deploy_stdout.splitlines()[1:]) if deploy_stdout else ""
        )
        if deploy_no_header.strip():
            pending = self.deployment_pending_summary(deploy_no_header)
            self.info(f"Deployment readiness summary: pending={pending}")

    def _snapshot_events(self, kubectl: list[str], cfg: WatchInstallConfig) -> None:
        self.info("Recent warning events")
        events_proc = run_command(
            [
                *kubectl, "-n", cfg.namespace,
                "get", "events", "--sort-by=.lastTimestamp",
            ],
            check=False,
        )
        events_lines = (events_proc.stdout or "").splitlines()
        filtered: list[str] = []
        for idx, line in enumerate(events_lines):
            if idx == 0:
                filtered.append(line)
                continue
            if any(token in line for token in _WARNING_EVENT_TOKENS):
                filtered.append(line)
        for line in filtered[-cfg.event_lines:]:
            print(line)

    def _snapshot_jobs_and_logs(
        self, kubectl: list[str], cfg: WatchInstallConfig,
    ) -> None:
        self.info("Bootstrap job status")
        jobs_proc = run_command(
            [
                *kubectl, "-n", cfg.namespace,
                "get", "jobs", "--sort-by=.metadata.creationTimestamp",
            ],
            check=False,
        )
        jobs_lines = (jobs_proc.stdout or "").splitlines()
        for line in jobs_lines[-_RECENT_JOB_TAIL_COUNT:]:
            print(line)

        pod_proc = run_command(
            [
                *kubectl, "-n", cfg.namespace, "get", "pods",
                "-l", _BOOTSTRAP_POD_SELECTOR,
                "-o", "jsonpath={.items[0].metadata.name}",
            ],
            check=False,
        )
        bootstrap_pod = (pod_proc.stdout or "").strip()
        if bootstrap_pod:
            self.info(f"Tail bootstrap pod logs: {bootstrap_pod}")
            logs_proc = run_command(
                [
                    *kubectl, "-n", cfg.namespace, "logs", bootstrap_pod,
                    f"--tail={cfg.job_log_lines}",
                ],
                check=False,
            )
            if logs_proc.stdout:
                sys.stdout.write(logs_proc.stdout)
            if logs_proc.stderr:
                sys.stderr.write(logs_proc.stderr)

    def run(self, cfg: WatchInstallConfig, sleep_fn: Callable[[float], None] | None = None) -> int:
        sleep = sleep_fn if sleep_fn is not None else time.sleep
        kubectl = kube_cmd()
        ns_check = run_command(
            [*kubectl, "get", "namespace", cfg.namespace], check=False,
        )
        if ns_check.returncode != 0:
            raise ConfigError(f"Namespace '{cfg.namespace}' not found.")

        if cfg.once:
            self.snapshot(kubectl, cfg)
            return 0

        self.info("Starting watcher; press Ctrl+C to stop.")
        while True:
            self.snapshot(kubectl, cfg)
            sleep(cfg.interval_seconds)


__all__ = ["WatchInstallConfig", "WatchInstallRunner"]
