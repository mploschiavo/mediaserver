"""Atomic JSON editor with backup + validation + rollback.

Mirrors ``SafeYamlEditor`` but for JSON-backed state files such as the
controller's ``bans.json``. JSON was chosen for that file because it's
machine-written much more often than human-edited, and ``json`` is in
the stdlib — no PyYAML dependency for something callers don't diff by
hand.

Uses the same safety model as the YAML editor: file lock via fcntl,
atomic temp file + fsync + rename, backup-before-write, validator hook.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class SafeJsonEditError(RuntimeError):
    pass


class SafeJsonEditor:
    """Edit a single JSON file atomically with optional validation.

    ``validator`` receives the post-mutation payload and may raise any
    exception; it is re-wrapped as ``SafeJsonEditError``. The mutator
    must return a JSON-serialisable ``dict``.
    """

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

    def read(self) -> dict[str, Any]:
        """Return the current parsed payload (empty dict if file missing)."""
        if not self._path.is_file():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SafeJsonEditError(f"could not read {self._path}: {exc}") from exc
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SafeJsonEditError(
                f"current file is not valid JSON ({self._path}): {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise SafeJsonEditError(f"top-level JSON must be an object, got {type(data).__name__}")
        return data

    def edit(self, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.with_suffix(self._path.suffix + ".lock")
        with lock_path.open("w") as lock_fh:
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX)
            except OSError as exc:
                raise SafeJsonEditError(f"could not acquire lock: {exc}") from exc
            try:
                return self._do_edit(mutator)
            finally:
                try:
                    fcntl.flock(lock_fh, fcntl.LOCK_UN)
                except OSError:
                    logging.getLogger("media_stack").debug(
                        "[DEBUG] Swallowed exception", exc_info=True
                    )

    def _do_edit(self, mutator: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        current = self.read()
        new_data = self._run_mutator(mutator, current)
        self._run_validator(new_data)
        backup = self._backup()
        self._atomic_write(new_data, backup)
        return new_data

    def _run_mutator(
        self,
        mutator: Callable[[dict[str, Any]], dict[str, Any]],
        current: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            new_data = mutator(current)
        except Exception as exc:  # noqa: BLE001
            raise SafeJsonEditError(f"mutator raised: {exc}") from exc
        if new_data is None:
            new_data = {}
        if not isinstance(new_data, dict):
            raise SafeJsonEditError("mutator must return a dict")
        return new_data

    def _run_validator(self, new_data: dict[str, Any]) -> None:
        if self._validator is None:
            return
        try:
            self._validator(new_data)
        except Exception as exc:  # noqa: BLE001
            raise SafeJsonEditError(f"validation failed: {exc}") from exc

    def _atomic_write(self, new_data: dict[str, Any], backup: Path | None) -> None:
        try:
            rendered = json.dumps(new_data, indent=2, sort_keys=False, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise SafeJsonEditError(f"could not serialize new JSON: {exc}") from exc

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
            raise SafeJsonEditError(f"atomic write failed: {exc}") from exc


__all__ = ["SafeJsonEditor", "SafeJsonEditError"]
