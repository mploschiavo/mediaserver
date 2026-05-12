"""RestoreStackRunner — restore a stack backup archive.

ADR-0015 Phase 7l. Pre-Phase-7l ``RestoreStackCommand`` lived in
commands/. The workflow (tar extract → file-tree copy back into
stack-root → kubectl-apply secret YAML) is workflow material.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from media_stack.core.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import ConfigError


_SECRET_FILENAME = "media-stack-secrets.yaml"


class RestoreStackRunner:
    """Workflow: extract archive → restore config/data → reapply secret."""

    def env_bool(self, name: str, default: bool) -> bool:
        raw = str(
            os.environ.get(name, "1" if default else "0") or "",
        ).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def copy_tree_contents(self, src: Path, dst: Path) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            target = dst / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)

    def run(self, args: argparse.Namespace) -> int:
        archive_path = Path(args.archive_path).expanduser().resolve()
        if not archive_path.exists():
            raise ConfigError(f"Backup archive not found: {archive_path}")
        kubectl = kube_cmd()
        stack_root = Path(args.stack_root).expanduser().resolve()
        stack_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="media-stack-restore-") as tmpdir:
            tmp_path = Path(tmpdir)
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(tmp_path)
            roots = [p for p in tmp_path.iterdir() if p.is_dir()]
            if not roots:
                raise ConfigError("Invalid backup archive structure.")
            restore_root = roots[0]
            for folder in ("config", "data"):
                source = restore_root / folder
                if source.exists() and source.is_dir():
                    self.copy_tree_contents(source, stack_root / folder)
            if args.restore_media:
                media_src = restore_root / "media"
                if media_src.exists() and media_src.is_dir():
                    self.copy_tree_contents(media_src, stack_root / "media")
            secret_file = restore_root / _SECRET_FILENAME
            if secret_file.exists():
                run_command(
                    [*kubectl, "apply", "-f", str(secret_file)], check=True,
                )
        print(f"[OK] Restore complete from {archive_path}")
        return 0


__all__ = ["RestoreStackRunner"]
