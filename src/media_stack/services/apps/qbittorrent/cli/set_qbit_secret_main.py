#!/usr/bin/env python3
"""Set stack-admin/qB credentials in media-stack-secrets.

Behavior is intentionally backward-compatible with bin/set-qbit-secret.sh.

Note: kept at the legacy ``services/apps/qbittorrent/cli/`` path
because tests load this file directly by absolute path and patch
helpers in its module namespace. Phase 16-D batch 3 (download
clients — qbittorrent) deliberately leaves the CLI helpers in
place; Phase 16-F or a follow-up batch will revisit once the
file-path-based test loaders are migrated to import the new module
path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass

from media_stack.core.exceptions import ConfigError, MediaStackError
from media_stack.core.platforms.kubernetes.kube_client import resolve_kubectl_binary


@dataclass(frozen=True)
class SetQbitSecretConfig:
    namespace: str
    username: str
    password: str


class SetQbitSecretCommand:
    """CLI command that creates or updates stack-admin credentials in
    the ``media-stack-secrets`` Kubernetes Secret."""

    SECRET_NAME = "media-stack-secrets"
    DEFAULT_NAMESPACE = "media-stack"
    DEFAULT_USERNAME = "admin"

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="bin/set-qbit-secret.sh",
            description=(
                "Set or update stack admin credentials in media-stack-secrets. "
                "qBittorrent defaults to STACK_ADMIN_*."
            ),
        )
        parser.add_argument("username", nargs="?", default="")
        parser.add_argument("password", nargs="?", default="")
        return parser

    def parse_config(self, argv: list[str] | None = None) -> SetQbitSecretConfig:
        args = self.build_parser().parse_args(argv)

        env = os.environ
        namespace = str(env.get("NAMESPACE", self.DEFAULT_NAMESPACE)).strip() or self.DEFAULT_NAMESPACE
        username = str(args.username or env.get("STACK_ADMIN_USERNAME") or self.DEFAULT_USERNAME).strip()
        password = str(args.password or env.get("STACK_ADMIN_PASSWORD") or namespace).strip()
        if not password:
            password = namespace

        return SetQbitSecretConfig(
            namespace=namespace,
            username=username,
            password=password,
        )

    def run_subprocess(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=True,
            text=True,
            input=input_text,
        )

    def secret_exists(self, kubectl: list[str], namespace: str) -> bool:
        proc = self.run_subprocess(
            [*kubectl, "-n", namespace, "get", "secret", self.SECRET_NAME],
            check=False,
        )
        return proc.returncode == 0

    def apply_manifest(self, kubectl: list[str], manifest: str) -> None:
        proc = self.run_subprocess([*kubectl, "apply", "-f", "-"], input_text=manifest)
        if proc.stdout.strip():
            print(proc.stdout.strip())

    def patch_secret(
        self, kubectl: list[str], namespace: str, payload: dict[str, object]
    ) -> None:
        patch = json.dumps(payload, separators=(",", ":"))
        self.run_subprocess(
            [
                *kubectl,
                "-n",
                namespace,
                "patch",
                "secret",
                self.SECRET_NAME,
                "--type",
                "merge",
                "-p",
                patch,
            ]
        )

    def run(self, cfg: SetQbitSecretConfig) -> int:
        # NOTE: ADR-0012 design principle 3 — route through module-level
        # aliases so ``mock.patch.object(mod, "_secret_exists", ...)`` and
        # ``mock.patch.object(mod, "_patch_secret", ...)`` keep intercepting.
        module = sys.modules[__name__]
        try:
            resolve_kubectl = getattr(module, "resolve_kubectl_binary")
            kubectl = resolve_kubectl()
        except ConfigError as exc:
            raise MediaStackError(str(exc)) from exc

        secret_exists_alias = getattr(module, "_secret_exists")
        apply_manifest_alias = getattr(module, "_apply_manifest")
        patch_secret_alias = getattr(module, "_patch_secret")

        if not secret_exists_alias(kubectl, cfg.namespace):
            manifest = f"""apiVersion: v1
kind: Secret
metadata:
  name: {self.SECRET_NAME}
  namespace: {cfg.namespace}
type: Opaque
stringData:
  STACK_ADMIN_USERNAME: "{cfg.username}"
  STACK_ADMIN_PASSWORD: "{cfg.password}"
  JELLYFIN_API_KEY: ""
  JELLYFIN_USER_ID: ""
"""
            apply_manifest_alias(kubectl, manifest)
            print(
                f"[OK] Created {cfg.namespace}/{self.SECRET_NAME} with stack admin credentials."
            )
            return 0

        string_data: dict[str, str] = {
            "STACK_ADMIN_USERNAME": cfg.username,
            "STACK_ADMIN_PASSWORD": cfg.password,
        }
        patch_secret_alias(kubectl, cfg.namespace, {"stringData": string_data})
        print(
            f"[OK] Updated stack admin credentials in {cfg.namespace}/{self.SECRET_NAME}."
        )
        return 0

    def main(self, argv: list[str] | None = None) -> int:
        try:
            cfg = self.parse_config(argv)
            return self.run(cfg)
        except (ConfigError, MediaStackError) as exc:
            print(f"[ERR] {exc}", file=sys.stderr)
            return 1


_COMMAND = SetQbitSecretCommand()
_build_parser = _COMMAND.build_parser
parse_config = _COMMAND.parse_config
_run = _COMMAND.run_subprocess
_secret_exists = _COMMAND.secret_exists
_apply_manifest = _COMMAND.apply_manifest
_patch_secret = _COMMAND.patch_secret
run = _COMMAND.run
main = _COMMAND.main


if __name__ == "__main__":
    raise SystemExit(main())
