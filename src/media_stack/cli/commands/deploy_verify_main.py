#!/usr/bin/env python3
"""Deterministic deploy + verification orchestration.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from media_stack.core.exceptions import ConfigError, MediaStackError


def ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}")


def _run(script_path: Path, *args: str, env: dict[str, str] | None = None) -> None:
    command = [str(script_path), *args]
    proc = subprocess.run(
        command,
        check=False,
        text=True,
        env=(dict(os.environ) | dict(env or {})),
    )
    if proc.returncode != 0:
        raise MediaStackError(f"Command failed (exit={proc.returncode}): {' '.join(command)}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bin/deploy-verify.sh",
        description=(
            "End-to-end deterministic deploy runner: install/bootstrap, flow verify, "
            "smoke checks, optional Playwright smoke, final status snapshot."
        ),
    )
    parser.add_argument("node_ip", nargs="?")
    parser.add_argument("namespace", nargs="?", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument("profile", nargs="?", default=os.environ.get("PROFILE", "full"))
    parser.add_argument(
        "--ingress-domain",
        default=os.environ.get("INGRESS_DOMAIN", "local"),
    )
    parser.add_argument(
        "--run-playwright",
        action="store_true",
        default=str(os.environ.get("RUN_PLAYWRIGHT", "0")).strip() == "1",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    node_ip = str(args.node_ip or os.environ.get("NODE_IP") or "").strip()
    if not node_ip:
        raise ConfigError("NODE_IP is required")

    namespace = str(args.namespace or "").strip()
    profile = str(args.profile or "").strip()
    ingress_domain = str(args.ingress_domain or "").strip()
    if profile not in {"minimal", "full", "public-demo", "power-user"}:
        raise ConfigError(
            f"Unsupported profile '{profile}'. Use minimal|full|public-demo|power-user."
        )

    root_dir = Path(__file__).resolve().parents[2]
    scripts_dir = root_dir / "bin"

    info("Starting deploy and verification")
    info(f"Node IP: {node_ip}")
    info(f"Namespace: {namespace}")
    info(f"Profile: {profile}")
    info(f"Ingress domain: {ingress_domain}")

    info("Phase 1/5: install and bootstrap")
    _run(
        scripts_dir / "install.sh",
        "--profile",
        profile,
        "--namespace",
        namespace,
        "--ingress-domain",
        ingress_domain,
        "--node-ip",
        node_ip,
    )

    info("Phase 2/5: verify end-to-end flow")
    _run(scripts_dir / "verify-flow.sh", namespace)

    info("Phase 3/5: ingress smoke test")
    _run(scripts_dir / "microk8s-smoke-test.sh", node_ip, namespace)

    if bool(args.run_playwright):
        info("Phase 4/5: Playwright ingress smoke")
        _run(scripts_dir / "run-playwright-smoke.sh", node_ip, namespace)
    else:
        info("Phase 4/5: Playwright ingress smoke skipped (RUN_PLAYWRIGHT=0)")

    info("Phase 5/5: final status snapshot")
    _run(scripts_dir / "stack-status.sh", env={"NAMESPACE": namespace})

    print()
    print(f"[OK] Deploy + verification complete for namespace '{namespace}'.")
    print("[INFO] Render hosts entries if needed:")
    print(f"  bash bin/render-hosts-example.sh {node_ip} {namespace}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, MediaStackError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        sys.exit(1)
