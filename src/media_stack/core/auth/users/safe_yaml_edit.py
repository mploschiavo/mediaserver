"""Atomic YAML editor with backup + validation + rollback.

Used for Authelia's users_database.yml, where a corrupt write takes the
whole auth layer down. Reusable for any single-file YAML config where
concurrent edits and partial writes must not happen.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml
import logging


class SafeYamlEditError(RuntimeError):
    pass


class SafeYamlEditor:

    def __init__(self, path: Path, validator: Callable[[Any], None] | None = None) -> None:
        self._path = Path(path)
        self._validator = validator

    def _now_stamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def _backup(self) -> Path | None:
        if not self._path.is_file():
            return None
        backup_dir = self._path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{self._path.name}.{self._now_stamp()}"
        shutil.copy2(self._path, backup_path)
        return backup_path

    def edit(self, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with lock_path.open("w") as lock_fh:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX)
            except OSError as exc:
                raise SafeYamlEditError(f"could not acquire lock: {exc}") from exc
            try:
                return self._do_edit(mutator)
            finally:
                try:
                    fcntl.flock(lock_fh, fcntl.LOCK_UN)
                except OSError:
                    logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)

    def _do_edit(self, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        current = self._read_current()
        new_data = self._run_mutator(mutator, current)
        self._run_validator(new_data)
        backup = self._backup()
        self._atomic_write(new_data, backup)
        return new_data

    def _read_current(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {}
        try:
            return yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise SafeYamlEditError(f"current file is not valid YAML: {exc}") from exc

    def _run_mutator(self, mutator, current):
        try:
            new_data = mutator(current) or {}
        except Exception as exc:  # noqa: BLE001
            raise SafeYamlEditError(f"mutator raised: {exc}") from exc
        if not isinstance(new_data, dict):
            raise SafeYamlEditError("mutator must return a dict")
        return new_data

    def _run_validator(self, new_data: dict[str, Any]) -> None:
        if self._validator is None:
            return
        try:
            self._validator(new_data)
        except Exception as exc:  # noqa: BLE001
            raise SafeYamlEditError(f"validation failed: {exc}") from exc

    def _atomic_write(self, new_data: dict[str, Any], backup: Path | None) -> None:
        try:
            rendered = yaml.safe_dump(
                new_data,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
        except yaml.YAMLError as exc:
            raise SafeYamlEditError(f"could not serialize new YAML: {exc}") from exc

        fd, tmp_path = tempfile.mkstemp(
            prefix=self._path.name + ".",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(rendered)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception as exc:  # noqa: BLE001
            Path(tmp_path).unlink(missing_ok=True)
            if backup and self._path.is_file():
                shutil.copy2(backup, self._path)
            raise SafeYamlEditError(f"atomic write failed: {exc}") from exc
