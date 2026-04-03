#!/usr/bin/env python3
"""Apply Kubernetes deployment scale policy guardrails."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cli.bootstrap_component_resolver import resolve_bootstrap_component_plan
from cli.cli_common import kube_cmd, run_command

from core.exceptions import MediaStackError


def _env_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _deployment_exists(kubectl: list[str], namespace: str, name: str) -> bool:
    proc = run_command(
        [*kubectl, "-n", namespace, "get", "deploy", name],
        check=False,
    )
    return proc.returncode == 0


def _current_replicas(kubectl: list[str], namespace: str, name: str) -> int:
    proc = run_command(
        [
            *kubectl,
            "-n",
            namespace,
            "get",
            "deploy",
            name,
            "-o",
            "jsonpath={.spec.replicas}",
        ],
        check=False,
    )
    text = str(proc.stdout or "").strip()
    try:
        return int(text)
    except Exception:
        return 1


def _scale_deployment(
    kubectl: list[str],
    *,
    namespace: str,
    name: str,
    replicas: int,
    dry_run: bool,
) -> None:
    if not _deployment_exists(kubectl, namespace, name):
        return
    if dry_run:
        print(f"[DRY] scale deploy/{name} -> {replicas}")
        return
    run_command(
        [
            *kubectl,
            "-n",
            namespace,
            "scale",
            "deploy",
            name,
            f"--replicas={int(replicas)}",
        ],
        check=True,
    )
    print(f"[OK] scale deploy/{name} -> {replicas}")


def _default_config_file() -> Path:
    env_path = str(os.environ.get("CONFIG_FILE", "")).strip()
    if env_path:
        return Path(env_path)
    root_dir = Path(__file__).resolve().parents[4]
    return root_dir / "bootstrap" / "media-stack.bootstrap.json"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/apply-scale-policy.sh",
        description=(
            "Enforce scale policy: keep managed apps at replicas>=1 and optionally "
            "scale configured apps to 0."
        ),
    )
    parser.add_argument(
        "config_file",
        nargs="?",
        default=str(_default_config_file()),
        help="Bootstrap config JSON path",
    )
    parser.add_argument("--namespace", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--scale-to-zero",
        dest="scale_to_zero",
        action="store_true",
        default=_env_truthy(os.environ.get("SCALE_TO_ZERO")),
        help="Scale apps listed in adapter_hooks.scale_policy.scale_to_zero_apps to 0 replicas.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    config_file = Path(str(args.config_file)).resolve()
    namespace = str(args.namespace or "").strip()
    if not namespace:
        raise MediaStackError("NAMESPACE must be non-empty")

    plan = resolve_bootstrap_component_plan(config_file)
    managed_apps = tuple(plan.managed_apps)
    scale_to_zero_apps = tuple(app for app in plan.scale_to_zero_apps if app in managed_apps)

    kubectl = kube_cmd()

    if managed_apps:
        print(f"[INFO] Managed apps from config: {', '.join(managed_apps)}")
    for app in managed_apps:
        if not _deployment_exists(kubectl, namespace, app):
            continue
        replicas = _current_replicas(kubectl, namespace, app)
        if replicas <= 0:
            _scale_deployment(
                kubectl,
                namespace=namespace,
                name=app,
                replicas=1,
                dry_run=bool(args.dry_run),
            )

    if bool(args.scale_to_zero):
        if scale_to_zero_apps:
            print(f"[INFO] Scale-to-zero apps from config: {', '.join(scale_to_zero_apps)}")
        for app in scale_to_zero_apps:
            _scale_deployment(
                kubectl,
                namespace=namespace,
                name=app,
                replicas=0,
                dry_run=bool(args.dry_run),
            )

    print("[OK] Scale policy applied.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except MediaStackError as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        sys.exit(1)
