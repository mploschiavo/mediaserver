#!/usr/bin/env python3
"""Restore a stack backup archive."""

from __future__ import annotations

import argparse
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from media_stack.core.exceptions import ConfigError

from media_stack.cli.workflows.cli_common import kube_cmd, run_command






class RestoreStackCommand:
    """Wraps restore stack CLI entrypoint."""

    def parse_args(self, argv: list[str] | None = None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            prog="bin/restore-stack.sh",
            description="Restores a backup produced by bin/backup-stack.sh.",
        )
        parser.add_argument("archive_path", help="Path to backup archive .tar.gz")
        parser.add_argument("--namespace", default=(os.environ.get("NAMESPACE", "media-stack") or "media-stack"))
        parser.add_argument("--stack-root", default=(os.environ.get("STACK_ROOT", "/srv/media-stack") or "/srv/media-stack"))
        parser.add_argument("--restore-media", action="store_true", default=_env_bool("RESTORE_MEDIA", False))
        return parser.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        args = self.parse_args(argv)
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
                    _copy_tree_contents(source, stack_root / folder)
            if args.restore_media:
                media_src = restore_root / "media"
                if media_src.exists() and media_src.is_dir():
                    _copy_tree_contents(media_src, stack_root / "media")
            secret_file = restore_root / "media-stack-secrets.yaml"
            if secret_file.exists():
                run_command([*kubectl, "apply", "-f", str(secret_file)], check=True)
        print(f"[OK] Restore complete from {archive_path}")
        return 0


    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _copy_tree_contents(src: Path, dst: Path) -> None:
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            target = dst / child.name
            if child.is_dir():
                shutil.copytree(child, target, dirs_exist_ok=True)
            else:
                shutil.copy2(child, target)


_instance = RestoreStackCommand()
parse_args = _instance.parse_args
main = _instance.main

if __name__ == "__main__":
    raise SystemExit(main())
_env_bool = _instance._env_bool
_copy_tree_contents = _instance._copy_tree_contents
