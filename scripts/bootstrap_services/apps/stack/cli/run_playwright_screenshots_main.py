#!/usr/bin/env python3
"""Capture Playwright screenshots for app UIs.

Media Automation Stack by Matthew Loschiavo:
https://matthewloschiavo.com
"""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

from core.exceptions import ConfigError, MediaStackError
from cli.cli_common import repo_root_from_script_file


def info(message: str) -> None:
    print(f"[INFO] {message}")


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=(dict(os.environ) | dict(env or {})),
        check=False,
        text=True,
        capture_output=False,
    )
    if check and proc.returncode != 0:
        raise MediaStackError(f"Command failed (exit={proc.returncode}): {' '.join(command)}")
    return proc


def _capture(command: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        env=dict(os.environ),
        check=False,
        text=True,
        capture_output=True,
    )


def _secret_value(namespace: str, secret_name: str, key: str) -> str:
    proc = _capture(
        [
            "kubectl",
            "-n",
            namespace,
            "get",
            "secret",
            secret_name,
            "-o",
            f"jsonpath={{.data.{key}}}",
        ]
    )
    if proc.returncode != 0:
        return ""
    b64 = str(proc.stdout or "").strip()
    if not b64:
        return ""
    try:
        return base64.b64decode(b64.encode("utf-8")).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _hosts_csv(root_dir: Path, node_ip: str, namespace: str) -> str:
    proc = _capture(
        [
            "bash",
            str(root_dir / "scripts" / "render-hosts-example.sh"),
            node_ip,
            namespace,
        ]
    )
    if proc.returncode != 0:
        raise MediaStackError(proc.stderr.strip() or "Failed rendering hosts example")
    line = str(proc.stdout or "").strip()
    if not line:
        return ""
    parts = line.split()
    if len(parts) <= 1:
        return ""
    return ",".join(parts[1:])


def _ensure_node_modules(playwright_dir: Path) -> None:
    if (playwright_dir / "node_modules").is_dir():
        return
    proc = _run(["npm", "ci"], cwd=playwright_dir, check=False)
    if proc.returncode == 0:
        return
    _run(["npm", "install"], cwd=playwright_dir, check=True)


def _run_playwright_capture(playwright_dir: Path, env: dict[str, str]) -> None:
    cmd = [
        "npx",
        "playwright",
        "test",
        "tests/screenshot-capture.spec.ts",
        "--reporter=list",
        "--workers=1",
    ]
    proc = _run(cmd, cwd=playwright_dir, env=env, check=False)
    if proc.returncode == 0:
        return

    _run(
        ["npx", "playwright", "install", "chromium"],
        cwd=playwright_dir,
        env=env,
        check=False,
    )
    _run(cmd, cwd=playwright_dir, env=env, check=True)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/run-playwright-screenshots.sh",
        description="Capture app screenshots using Playwright.",
    )
    parser.add_argument("node_ip", nargs="?", default=os.environ.get("STACK_NODE_IP", ""))
    parser.add_argument("namespace", nargs="?", default=os.environ.get("NAMESPACE", "media-stack"))
    parser.add_argument(
        "out_dir",
        nargs="?",
        default=os.environ.get(
            "STACK_SCREENSHOT_DIR",
            "",
        ),
    )
    parser.add_argument(
        "--secret-name",
        default=os.environ.get("STACK_SECRET_NAME", "media-stack-secrets"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    node_ip = str(args.node_ip or "").strip()
    namespace = str(args.namespace or "").strip()
    if not node_ip:
        raise ConfigError(
            "Missing NODE_IP. Usage: scripts/run-playwright-screenshots.sh <NODE_IP> [NAMESPACE] [OUT_DIR]"
        )
    if not namespace:
        raise ConfigError("NAMESPACE must be non-empty")

    root_dir = repo_root_from_script_file(__file__)
    out_dir = str(args.out_dir or "").strip() or str(root_dir / "docs" / "screenshots" / "apps")
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    hosts_csv = _hosts_csv(root_dir, node_ip, namespace)

    stack_user = os.environ.get("STACK_ADMIN_USERNAME") or _secret_value(
        namespace, str(args.secret_name), "STACK_ADMIN_USERNAME"
    )
    stack_pass = os.environ.get("STACK_ADMIN_PASSWORD") or _secret_value(
        namespace, str(args.secret_name), "STACK_ADMIN_PASSWORD"
    )

    jellyseerr_user = os.environ.get("JELLYSEERR_USERNAME") or stack_user
    jellyseerr_pass = os.environ.get("JELLYSEERR_PASSWORD") or stack_pass
    sab_user = os.environ.get("SABNZBD_USERNAME") or stack_user
    sab_pass = os.environ.get("SABNZBD_PASSWORD") or stack_pass

    info("Capturing Playwright UI screenshots")
    info(f"NODE_IP: {node_ip}")
    info(f"NAMESPACE: {namespace}")
    info(f"OUT_DIR: {out_path}")
    info(f"HOSTS: {hosts_csv}")
    if stack_user:
        info(f"Screenshot auth user: {stack_user}")

    playwright_dir = root_dir / "tests" / "e2e" / "playwright"
    _ensure_node_modules(playwright_dir)

    env = {
        "STACK_NODE_IP": node_ip,
        "STACK_HOSTS": hosts_csv,
        "STACK_SCREENSHOT_DIR": str(out_path),
        "STACK_ADMIN_USERNAME": stack_user,
        "STACK_ADMIN_PASSWORD": stack_pass,
        "JELLYSEERR_USERNAME": jellyseerr_user,
        "JELLYSEERR_PASSWORD": jellyseerr_pass,
        "SABNZBD_USERNAME": sab_user,
        "SABNZBD_PASSWORD": sab_pass,
    }
    _run_playwright_capture(playwright_dir, env)

    print("[OK] Screenshot capture complete.")
    for image in sorted(out_path.glob("*.png")):
        print(str(image))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, MediaStackError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        sys.exit(1)
