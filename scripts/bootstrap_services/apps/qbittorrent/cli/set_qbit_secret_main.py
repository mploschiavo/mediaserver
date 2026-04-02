#!/usr/bin/env python3
"""Set stack-admin/qB credentials in media-stack-secrets.

Behavior is intentionally backward-compatible with scripts/set-qbit-secret.sh.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass

from core.exceptions import ConfigError, MediaStackError
from core.platforms.kubernetes.kube_client import resolve_kubectl_binary


@dataclass(frozen=True)
class SetQbitSecretConfig:
    namespace: str
    username: str
    password: str


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scripts/set-qbit-secret.sh",
        description=(
            "Set or update stack admin credentials in media-stack-secrets. "
            "qBittorrent defaults to STACK_ADMIN_*."
        ),
    )
    parser.add_argument("username", nargs="?", default="")
    parser.add_argument("password", nargs="?", default="")
    return parser


def parse_config(argv: list[str] | None = None) -> SetQbitSecretConfig:
    args = _build_parser().parse_args(argv)

    namespace = (
        str(__import__("os").environ.get("NAMESPACE", "media-stack")).strip() or "media-stack"
    )
    env = __import__("os").environ
    username = str(args.username or env.get("STACK_ADMIN_USERNAME") or "admin").strip()
    password = str(args.password or env.get("STACK_ADMIN_PASSWORD") or namespace).strip()
    if not password:
        password = namespace

    return SetQbitSecretConfig(
        namespace=namespace,
        username=username,
        password=password,
    )


def _run(
    cmd: list[str], *, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
    )


def _secret_exists(kubectl: list[str], namespace: str) -> bool:
    proc = _run([*kubectl, "-n", namespace, "get", "secret", "media-stack-secrets"], check=False)
    return proc.returncode == 0


def _apply_manifest(kubectl: list[str], manifest: str) -> None:
    proc = _run([*kubectl, "apply", "-f", "-"], input_text=manifest)
    if proc.stdout.strip():
        print(proc.stdout.strip())


def _patch_secret(kubectl: list[str], namespace: str, payload: dict[str, object]) -> None:
    patch = json.dumps(payload, separators=(",", ":"))
    _run(
        [
            *kubectl,
            "-n",
            namespace,
            "patch",
            "secret",
            "media-stack-secrets",
            "--type",
            "merge",
            "-p",
            patch,
        ]
    )


def run(cfg: SetQbitSecretConfig) -> int:
    try:
        kubectl = resolve_kubectl_binary()
    except ConfigError as exc:
        raise MediaStackError(str(exc)) from exc

    if not _secret_exists(kubectl, cfg.namespace):
        manifest = f"""apiVersion: v1
kind: Secret
metadata:
  name: media-stack-secrets
  namespace: {cfg.namespace}
type: Opaque
stringData:
  STACK_ADMIN_USERNAME: "{cfg.username}"
  STACK_ADMIN_PASSWORD: "{cfg.password}"
  JELLYFIN_API_KEY: ""
  JELLYFIN_USER_ID: ""
"""
        _apply_manifest(kubectl, manifest)
        print(f"[OK] Created {cfg.namespace}/media-stack-secrets with stack admin credentials.")
        return 0

    string_data: dict[str, str] = {
        "STACK_ADMIN_USERNAME": cfg.username,
        "STACK_ADMIN_PASSWORD": cfg.password,
    }
    _patch_secret(kubectl, cfg.namespace, {"stringData": string_data})
    print(f"[OK] Updated stack admin credentials in {cfg.namespace}/media-stack-secrets.")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        cfg = parse_config(argv)
        return run(cfg)
    except (ConfigError, MediaStackError) as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
