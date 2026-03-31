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
from core.kube import resolve_kubectl_binary


@dataclass(frozen=True)
class SetQbitSecretConfig:
    namespace: str
    username: str
    password: str
    write_legacy_qbit_keys: bool


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

    namespace = str(__import__("os").environ.get("NAMESPACE", "media-stack")).strip() or "media-stack"
    default_stack_admin_user = (
        str(__import__("os").environ.get("DEFAULT_STACK_ADMIN_USER", "admin")).strip() or "admin"
    )
    default_stack_admin_pass = (
        str(__import__("os").environ.get("DEFAULT_STACK_ADMIN_PASS", "media-stack-admin")).strip()
        or "media-stack-admin"
    )
    write_legacy_qbit_keys = (
        str(__import__("os").environ.get("WRITE_LEGACY_QBIT_KEYS", "0")).strip().lower()
        in {"1", "true", "yes", "on"}
    )

    username = str(args.username or "").strip()
    password = str(args.password or "").strip()

    if not username and not password:
        username = default_stack_admin_user
        password = default_stack_admin_pass
        print("[INFO] Using default stack admin credentials from env defaults.")
    elif not username or not password:
        raise ConfigError("Provide both USERNAME and PASSWORD, or provide neither to use defaults.")

    return SetQbitSecretConfig(
        namespace=namespace,
        username=username,
        password=password,
        write_legacy_qbit_keys=write_legacy_qbit_keys,
    )


def _run(cmd: list[str], *, check: bool = True, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
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
  UNPACKERR_SONARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_RADARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_LIDARR_API_KEY: "replace-after-first-boot"
  UNPACKERR_READARR_API_KEY: "replace-after-first-boot"
"""
        _apply_manifest(kubectl, manifest)
        if cfg.write_legacy_qbit_keys:
            _patch_secret(
                kubectl,
                cfg.namespace,
                {
                    "stringData": {
                        "QBITTORRENT_USERNAME": cfg.username,
                        "QBITTORRENT_PASSWORD": cfg.password,
                    }
                },
            )
        print(f"[OK] Created {cfg.namespace}/media-stack-secrets with stack admin credentials.")
        return 0

    string_data: dict[str, str] = {
        "STACK_ADMIN_USERNAME": cfg.username,
        "STACK_ADMIN_PASSWORD": cfg.password,
    }
    if cfg.write_legacy_qbit_keys:
        string_data["QBITTORRENT_USERNAME"] = cfg.username
        string_data["QBITTORRENT_PASSWORD"] = cfg.password
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
