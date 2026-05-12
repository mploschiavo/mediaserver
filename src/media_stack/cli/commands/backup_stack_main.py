#!/usr/bin/env python3
"""Entry-point shim for ``bin/backup-stack.sh``.

ADR-0015 Phase 7l moved :class:`BackupStackRunner` to workflows/;
what remains is argparse + back-compat aliases.
"""

from __future__ import annotations

import argparse
import os

from media_stack.cli.workflows.backup_stack_runner import BackupStackRunner


class BackupStackEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = BackupStackRunner()

    @property
    def runner(self) -> BackupStackRunner:
        return self._runner

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/backup-stack.sh",
            description=(
                "Creates a backup bundle with stack config/data directories, "
                "optional media, and media-stack-secrets export."
            ),
        )
        parser.add_argument(
            "--namespace",
            default=(os.environ.get("NAMESPACE", "media-stack") or "media-stack"),
            help="Kubernetes namespace (env: NAMESPACE, default: media-stack)",
        )
        parser.add_argument(
            "--stack-root",
            default=(
                os.environ.get("STACK_ROOT", "/srv/media-stack")
                or "/srv/media-stack"
            ),
            help="Stack root path (env: STACK_ROOT, default: /srv/media-stack)",
        )
        parser.add_argument(
            "--backup-dir",
            default=(os.environ.get("BACKUP_DIR", "./backups") or "./backups"),
            help="Output backup directory (env: BACKUP_DIR, default: ./backups)",
        )
        parser.add_argument(
            "--include-media",
            action="store_true",
            default=self._runner.env_bool("INCLUDE_MEDIA", False),
            help="Include media directory in backup (env: INCLUDE_MEDIA=1)",
        )
        return parser.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        return self._runner.run(self.parse_args(argv))


_INSTANCE = BackupStackEntryPoint()
_RUNNER = _INSTANCE.runner
parse_args = _INSTANCE.parse_args
main = _INSTANCE.main
_env_bool = _RUNNER.env_bool
_copy_tree_if_exists = _RUNNER.copy_tree_if_exists


__all__ = [
    "BackupStackEntryPoint",
    "_copy_tree_if_exists",
    "_env_bool",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
