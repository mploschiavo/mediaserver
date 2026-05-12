"""BackupStackRunner — create stack backup archives.

ADR-0015 Phase 7l. Pre-Phase-7l ``BackupStackCommand`` lived in
commands/ with file-tree copy + kubectl secret export + tar.gz
bundle. The class is workflow material; Phase 7l moves it to
workflows/.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from media_stack.core.cli_common import kube_cmd, run_command
from media_stack.core.exceptions import MediaStackError


_SECRET_NAME = "media-stack-secrets"


class BackupStackRunner:
    """Workflow: snapshot config/data (+ optional media) + secret + tar.gz."""

    def env_bool(self, name: str, default: bool) -> bool:
        raw = str(
            os.environ.get(name, "1" if default else "0") or "",
        ).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def copy_tree_if_exists(self, src: Path, dst: Path) -> None:
        if not src.exists() or not src.is_dir():
            return
        shutil.copytree(src, dst, dirs_exist_ok=True)

    def run(self, args: argparse.Namespace) -> int:
        kubectl = kube_cmd()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_dir = Path(args.backup_dir).expanduser().resolve()
        bundle_dir = backup_dir / f"media-stack-backup-{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        bundle_dir.mkdir(parents=True, exist_ok=True)

        stack_root = Path(args.stack_root).expanduser().resolve()
        for folder in ("config", "data"):
            self.copy_tree_if_exists(stack_root / folder, bundle_dir / folder)

        if args.include_media:
            self.copy_tree_if_exists(stack_root / "media", bundle_dir / "media")

        self._export_secret(kubectl, args.namespace, bundle_dir)
        self._write_metadata(bundle_dir, timestamp, args, stack_root)

        archive_path = backup_dir / f"{bundle_dir.name}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)

        shutil.rmtree(bundle_dir, ignore_errors=True)
        if not archive_path.exists():
            raise MediaStackError(
                f"Failed creating backup archive: {archive_path}"
            )

        print(f"[OK] Backup created: {archive_path}")
        return 0

    def _export_secret(
        self, kubectl: list[str], namespace: str, bundle_dir: Path,
    ) -> None:
        secret_path = bundle_dir / f"{_SECRET_NAME}.yaml"
        secret_proc = run_command(
            [
                *kubectl, "-n", str(namespace), "get", "secret",
                _SECRET_NAME, "-o", "yaml",
            ],
            check=False,
        )
        if secret_proc.returncode == 0 and (secret_proc.stdout or "").strip():
            secret_path.write_text(secret_proc.stdout, encoding="utf-8")

    def _write_metadata(
        self,
        bundle_dir: Path,
        timestamp: str,
        args: argparse.Namespace,
        stack_root: Path,
    ) -> None:
        (bundle_dir / "backup-metadata.txt").write_text(
            "\n".join(
                [
                    f"timestamp={timestamp}",
                    f"namespace={args.namespace}",
                    f"stack_root={stack_root}",
                    f"include_media={1 if args.include_media else 0}",
                ]
            ) + "\n",
            encoding="utf-8",
        )


__all__ = ["BackupStackRunner"]
