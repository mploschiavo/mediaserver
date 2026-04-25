#!/usr/bin/env python3
"""Create stack backup archives."""

from __future__ import annotations

import argparse
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from media_stack.core.exceptions import MediaStackError

from media_stack.core.cli_common import kube_cmd, run_command






class BackupStackCommand:
    """Wraps backup stack CLI entrypoint."""

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
            default=(os.environ.get("STACK_ROOT", "/srv/media-stack") or "/srv/media-stack"),
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
            default=_env_bool("INCLUDE_MEDIA", False),
            help="Include media directory in backup (env: INCLUDE_MEDIA=1)",
        )
        return parser.parse_args(argv)

    def main(self, argv: list[str] | None = None) -> int:
        args = self.parse_args(argv)
        kubectl = kube_cmd()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup_dir = Path(args.backup_dir).expanduser().resolve()
        bundle_dir = backup_dir / f"media-stack-backup-{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        bundle_dir.mkdir(parents=True, exist_ok=True)

        stack_root = Path(args.stack_root).expanduser().resolve()
        for folder in ("config", "data"):
            _copy_tree_if_exists(stack_root / folder, bundle_dir / folder)

        if args.include_media:
            _copy_tree_if_exists(stack_root / "media", bundle_dir / "media")

        secret_path = bundle_dir / "media-stack-secrets.yaml"
        secret_proc = run_command(
            [*kubectl, "-n", str(args.namespace), "get", "secret", "media-stack-secrets", "-o", "yaml"],
            check=False,
        )
        if secret_proc.returncode == 0 and (secret_proc.stdout or "").strip():
            secret_path.write_text(secret_proc.stdout, encoding="utf-8")

        (bundle_dir / "backup-metadata.txt").write_text(
            "\n".join(
                [
                    f"timestamp={timestamp}",
                    f"namespace={args.namespace}",
                    f"stack_root={stack_root}",
                    f"include_media={1 if args.include_media else 0}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        archive_path = backup_dir / f"{bundle_dir.name}.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)

        shutil.rmtree(bundle_dir, ignore_errors=True)
        if not archive_path.exists():
            raise MediaStackError(f"Failed creating backup archive: {archive_path}")

        print(f"[OK] Backup created: {archive_path}")
        return 0


    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = str(os.environ.get(name, "1" if default else "0") or "").strip().lower()
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _copy_tree_if_exists(src: Path, dst: Path) -> None:
        if not src.exists() or not src.is_dir():
            return
        shutil.copytree(src, dst, dirs_exist_ok=True)


_instance = BackupStackCommand()
parse_args = _instance.parse_args
main = _instance.main
_env_bool = _instance._env_bool
_copy_tree_if_exists = _instance._copy_tree_if_exists


if __name__ == "__main__":
    raise SystemExit(main())
