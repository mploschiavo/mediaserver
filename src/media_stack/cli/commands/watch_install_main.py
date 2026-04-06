#!/usr/bin/env python3
"""Live install/bootstrap watcher for media-stack."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from dataclasses import dataclass

from media_stack.core.exceptions import ConfigError, MediaStackError

from media_stack.cli.workflows.cli_common import kube_cmd, run_command


@dataclass(frozen=True)
class WatchInstallConfig:
    namespace: str
    interval_seconds: int
    event_lines: int
    job_log_lines: int
    once: bool


def _ts() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")


def _info(message: str) -> None:
    print(f"[{_ts()}] [INFO] {message}")


def _warn(message: str) -> None:
    print(f"[{_ts()}] [WARN] {message}", file=sys.stderr)


def parse_config(argv: list[str] | None = None) -> WatchInstallConfig:
    parser = argparse.ArgumentParser(
        prog="bin/watch-install.sh",
        description=(
            "Live install/bootstrap watcher for media-stack "
            "(pods/deployments/events/bootstrap-job logs)."
        ),
    )
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument("--interval", type=int, default=int(os.environ.get("INTERVAL", "10")))
    parser.add_argument("--event-lines", type=int, default=int(os.environ.get("EVENT_LINES", "15")))
    parser.add_argument(
        "--job-log-lines", type=int, default=int(os.environ.get("JOB_LOG_LINES", "20"))
    )
    parser.add_argument("--once", action="store_true", default=False)
    args = parser.parse_args(argv)

    if args.interval < 1:
        raise ConfigError("--interval must be >= 1")
    if args.event_lines < 1:
        raise ConfigError("--event-lines must be >= 1")
    if args.job_log_lines < 1:
        raise ConfigError("--job-log-lines must be >= 1")

    return WatchInstallConfig(
        namespace=str(args.namespace or "").strip() or "media-stack",
        interval_seconds=int(args.interval),
        event_lines=int(args.event_lines),
        job_log_lines=int(args.job_log_lines),
        once=bool(args.once),
    )


def _print_command_output(cmd: list[str]) -> str:
    proc = run_command(cmd, check=False)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return stdout


def _pod_readiness_summary(pods_table: str) -> tuple[int, int]:
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
            except Exception:
                pass
        if status in {"CrashLoopBackOff", "Error", "ImagePullBackOff", "RunContainerError"}:
            unhealthy += 1
    return not_ready, unhealthy


def _deployment_pending_summary(deploy_table: str) -> int:
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
        except Exception:
            continue
    return pending


def _snapshot(kubectl: list[str], cfg: WatchInstallConfig) -> None:
    print("\n==================================================================")
    _info("Install Watch Snapshot")
    _info(f"Namespace: {cfg.namespace} | Refresh: {cfg.interval_seconds}s")

    _info("Pod status")
    pods_stdout = _print_command_output([*kubectl, "-n", cfg.namespace, "get", "pods"])
    pods_no_header = "\n".join(pods_stdout.splitlines()[1:]) if pods_stdout else ""
    if pods_no_header.strip():
        not_ready, unhealthy = _pod_readiness_summary(pods_no_header)
        _info(f"Pod readiness summary: not_ready={not_ready}, unhealthy={unhealthy}")
    else:
        _warn(f"No pods found in namespace {cfg.namespace}")

    _info("Deployment rollout status")
    deploy_stdout = _print_command_output([*kubectl, "-n", cfg.namespace, "get", "deploy"])
    deploy_no_header = "\n".join(deploy_stdout.splitlines()[1:]) if deploy_stdout else ""
    if deploy_no_header.strip():
        pending = _deployment_pending_summary(deploy_no_header)
        _info(f"Deployment readiness summary: pending={pending}")

    _info("Recent warning events")
    events_proc = run_command(
        [*kubectl, "-n", cfg.namespace, "get", "events", "--sort-by=.lastTimestamp"],
        check=False,
    )
    events_lines = (events_proc.stdout or "").splitlines()
    filtered: list[str] = []
    for idx, line in enumerate(events_lines):
        if idx == 0:
            filtered.append(line)
            continue
        if any(token in line for token in ("Warning", "Failed", "BackOff", "Unhealthy", "Error")):
            filtered.append(line)
    for line in filtered[-cfg.event_lines :]:
        print(line)

    _info("Bootstrap job status")
    jobs_proc = run_command(
        [*kubectl, "-n", cfg.namespace, "get", "jobs", "--sort-by=.metadata.creationTimestamp"],
        check=False,
    )
    jobs_lines = (jobs_proc.stdout or "").splitlines()
    for line in jobs_lines[-5:]:
        print(line)

    pod_proc = run_command(
        [
            *kubectl,
            "-n",
            cfg.namespace,
            "get",
            "pods",
            "-l",
            "app=media-stack-bootstrap",
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ],
        check=False,
    )
    bootstrap_pod = (pod_proc.stdout or "").strip()
    if bootstrap_pod:
        _info(f"Tail bootstrap pod logs: {bootstrap_pod}")
        logs_proc = run_command(
            [*kubectl, "-n", cfg.namespace, "logs", bootstrap_pod, f"--tail={cfg.job_log_lines}"],
            check=False,
        )
        if logs_proc.stdout:
            sys.stdout.write(logs_proc.stdout)
        if logs_proc.stderr:
            sys.stderr.write(logs_proc.stderr)


def run(cfg: WatchInstallConfig) -> int:
    kubectl = kube_cmd()
    ns_check = run_command([*kubectl, "get", "namespace", cfg.namespace], check=False)
    if ns_check.returncode != 0:
        raise ConfigError(f"Namespace '{cfg.namespace}' not found.")

    if cfg.once:
        _snapshot(kubectl, cfg)
        return 0

    _info("Starting watcher; press Ctrl+C to stop.")
    while True:
        _snapshot(kubectl, cfg)
        time.sleep(cfg.interval_seconds)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(parse_config(argv))
    except KeyboardInterrupt:
        return 130
    except (ConfigError, MediaStackError, OSError, ValueError) as exc:
        print(f"[{_ts()}] [ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
