#!/usr/bin/env python3
"""Collect bootstrap diagnostics for a namespace.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import os
import sys

from core.exceptions import MediaStackError

from cli.cli_common import kube_cmd, run_command


def _print_cmd_output(cmd: list[str]) -> None:
    proc = run_command(cmd, check=False)
    if proc.stdout:
        print(proc.stdout.rstrip())


def _restarted_pods(kubectl: list[str], namespace: str) -> list[str]:
    proc = run_command(
        [*kubectl, "-n", namespace, "get", "pods", "--no-headers"],
        check=False,
    )
    names: list[str] = []
    for line in str(proc.stdout or "").splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        try:
            restarts = int(cols[3])
        except Exception:
            continue
        if restarts > 0:
            names.append(cols[0])
    return names


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/bootstrap-debug.sh",
        description=(
            "Collect bootstrap diagnostics: workloads, events, bootstrap job logs, "
            "and previous logs for restarted pods."
        ),
    )
    parser.add_argument(
        "namespace",
        nargs="?",
        default=os.environ.get("NAMESPACE", "media-stack"),
    )
    parser.add_argument(
        "--tail-lines",
        type=int,
        default=int(os.environ.get("TAIL_LINES", "120")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    namespace = str(args.namespace or "").strip()
    if not namespace:
        raise MediaStackError("NAMESPACE must be non-empty")

    tail_lines = int(args.tail_lines)
    if tail_lines <= 0:
        raise MediaStackError("TAIL_LINES must be > 0")

    kubectl = kube_cmd()

    print(f"=== Namespace: {namespace} ===")
    print()
    print("== Workload Summary ==")
    _print_cmd_output([*kubectl, "-n", namespace, "get", "deploy,pods,job"])

    print()
    print("== Recent Events ==")
    events = run_command(
        [*kubectl, "-n", namespace, "get", "events", "--sort-by=.lastTimestamp"],
        check=False,
    )
    if events.stdout:
        lines = events.stdout.rstrip().splitlines()
        tail = lines[-200:] if len(lines) > 200 else lines
        print("\n".join(tail))

    print()
    print("== Bootstrap Job Logs ==")
    _print_cmd_output(
        [
            *kubectl,
            "-n",
            namespace,
            "logs",
            "job/media-stack-bootstrap",
            f"--tail={tail_lines}",
            "--timestamps",
        ]
    )
    _print_cmd_output(
        [
            *kubectl,
            "-n",
            namespace,
            "logs",
            "job/media-stack-prowlarr-auto-indexers",
            f"--tail={tail_lines}",
            "--timestamps",
        ]
    )

    print()
    print("== Restarted Pod Previous Logs ==")
    restarted = _restarted_pods(kubectl, namespace)
    if not restarted:
        print("No restarted pods found.")
        return 0

    for pod in restarted:
        print()
        print(f"--- Pod: {pod} (describe) ---")
        desc = run_command(
            [*kubectl, "-n", namespace, "describe", "pod", pod],
            check=False,
        )
        if desc.stdout:
            lines = desc.stdout.rstrip().splitlines()
            tail = lines[-120:] if len(lines) > 120 else lines
            print("\n".join(tail))

        print(f"--- Pod: {pod} (previous logs) ---")
        _print_cmd_output(
            [
                *kubectl,
                "-n",
                namespace,
                "logs",
                pod,
                "--previous",
                f"--tail={tail_lines}",
            ]
        )

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MediaStackError as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        sys.exit(1)
