"""Filesystem gateway for teardown workflows."""

from __future__ import annotations

import shutil
from pathlib import Path


class TeardownFileSystemService:
    """Filesystem operations isolated behind a service boundary."""

    def dir_size(self, path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        try:
            for child in path.rglob("*"):
                try:
                    if child.is_file():
                        total += child.stat().st_size
                except OSError:
                    continue
        except OSError:
            return 0
        return total

    def remove_tree(self, path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
            return
        if path.exists():
            shutil.rmtree(path, ignore_errors=False)
