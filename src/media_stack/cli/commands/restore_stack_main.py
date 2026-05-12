#!/usr/bin/env python3
"""Entry-point shim for ``bin/restore-stack.sh``.

ADR-0015 Phase 7l moved :class:`RestoreStackRunner` to workflows/.
"""

from __future__ import annotations

import argparse
import os

from media_stack.cli.workflows.restore_stack_runner import RestoreStackRunner


class RestoreStackEntryPoint:
    """Per-ADR-0012 entry-point: argparse → runner.run."""

    def __init__(self) -> None:
        self._runner = RestoreStackRunner()

    @property
    def runner(self) -> RestoreStackRunner:
        return self._runner

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/restore-stack.sh",
            description="Restores a backup produced by bin/backup-stack.sh.",
        )
        parser.add_argument("archive_path", help="Path to backup archive .tar.gz")
        parser.add_argument(
            "--namespace",
            default=(os.environ.get("NAMESPACE", "media-stack") or "media-stack"),
        )
        parser.add_argument(
            "--stack-root",
            default=(
                os.environ.get("STACK_ROOT", "/srv/media-stack")
                or "/srv/media-stack"
            ),
        )
        parser.add_argument(
            "--restore-media", action="store_true",
            default=self._runner.env_bool("RESTORE_MEDIA", False),
        )
        return parser.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        return self._runner.run(self.parse_args(argv))


_INSTANCE = RestoreStackEntryPoint()
_RUNNER = _INSTANCE.runner
parse_args = _INSTANCE.parse_args
main = _INSTANCE.main
_env_bool = _RUNNER.env_bool
_copy_tree_contents = _RUNNER.copy_tree_contents


__all__ = [
    "RestoreStackEntryPoint",
    "_copy_tree_contents",
    "_env_bool",
    "main",
    "parse_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
