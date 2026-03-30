"""Filesystem adapters and helpers."""

from __future__ import annotations

from pathlib import Path


class FileSystem:
    """Small filesystem adapter for improved testability."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def write_text(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        path.write_text(content, encoding=encoding)

    def write_text_atomic(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(content, encoding=encoding)
        tmp_path.replace(path)
