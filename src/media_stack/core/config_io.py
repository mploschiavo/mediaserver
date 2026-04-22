"""Safe read/write helpers for app config files.

Two failure modes we've seen in production drove this module:

1. Mid-flight corruption — Prowlarr's config.xml ended up with
   ``</Config>sm>\n</Config>`` after a pod restart caught the app
   mid-write. The previous regex-based file editor in
   ``services/apps/servarr/http_preflight.py`` couldn't detect the
   damage and would happily round-trip the corrupt bytes.

2. Partial writes — a crash mid-``write_text`` leaves a truncated
   file that next boot can't parse.

The helpers here:

- ``read_and_parse_xml`` returns the parsed tree or raises
  ``ConfigParseError`` with the exact diagnostic. No silent
  ``errors="replace"``.
- ``atomic_write_xml`` writes to a sibling temp file, fsyncs, renames
  into place, then **re-parses what landed on disk**. If the post-
  write parse fails, the original file is restored from a backup
  copy and the function raises.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET
import logging

_TMP_COUNTER_LOCK = threading.Lock()
_TMP_COUNTER = 0


def _unique_tmp_path(path: Path, label: str) -> Path:
    """Build a per-call unique sibling-temp path so concurrent
    ``atomic_write_xml`` calls on the same destination don't race
    on a shared ``.new`` file. Without this, the ARR preflight
    workers (4-way parallel) would step on each other:
    T1 writes ``.new`` + replaces (consuming ``.new``); T2 tries
    its own replace and ENOENT-fails because T1 just consumed the
    file at the same path."""
    global _TMP_COUNTER
    with _TMP_COUNTER_LOCK:
        _TMP_COUNTER += 1
        n = _TMP_COUNTER
    return path.with_suffix(
        f"{path.suffix}.{label}.{os.getpid()}.{int(time.time_ns())}.{n}"
    )


class ConfigParseError(Exception):
    """Raised when a config file on disk is not parseable."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def read_and_parse_xml(path: Path) -> ET.ElementTree:
    """Read and parse an XML config. Raises ``ConfigParseError`` if
    the file is missing, unreadable, or malformed.

    No ``errors='replace'`` — surrogate U+FFFD substitutions hide
    real corruption."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigParseError(path, f"missing: {exc}") from exc
    except OSError as exc:
        raise ConfigParseError(path, f"unreadable: {exc}") from exc

    try:
        return ET.ElementTree(ET.fromstring(raw))
    except ET.ParseError as exc:
        raise ConfigParseError(path, f"XML parse error: {exc}") from exc


def atomic_write_xml(
    path: Path,
    tree: ET.ElementTree,
    *,
    keep_backup: bool = True,
) -> None:
    """Serialize ``tree`` to ``path`` atomically and verify the result
    parses back cleanly.

    Steps:

    1. If the destination exists, copy it to ``path + ".bak"`` so we
       can roll back. The copy is only kept on success when
       ``keep_backup=True``; otherwise it's removed at the end.
    2. Write to ``path + ".new"``, ``fsync`` the file and the
       directory.
    3. Rename ``path.new`` -> ``path`` (POSIX-atomic).
    4. Re-read and re-parse ``path``. If the parse fails, restore
       from the backup and raise ``ConfigParseError``.

    The ``.new`` and ``.bak`` filenames are deliberately not the
    same as the temp suffix used by ``FileSystem.write_text_atomic``
    so a partially-rolled-back state is recognisable on disk.
    """
    path = Path(path)
    parent = path.parent
    backup_path: Optional[Path] = None

    if path.exists():
        backup_path = _unique_tmp_path(path, "bak")
        shutil.copy2(path, backup_path)

    tmp_path = _unique_tmp_path(path, "new")
    try:
        # ET.write defaults to xml_declaration=False; replicate the
        # bare-Config style the *arr apps write.
        with tmp_path.open("wb") as fh:
            tree.write(fh, encoding="utf-8", xml_declaration=False)
            fh.flush()
            os.fsync(fh.fileno())

        # fsync the directory so the rename is durable across crash.
        try:
            dir_fd = os.open(str(parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            # Some filesystems (overlayfs, tmpfs) reject directory
            # fsync; not fatal for correctness, just skips the
            # durability barrier.
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)

        os.replace(tmp_path, path)

        # Verify what landed on disk parses.
        try:
            ET.fromstring(path.read_bytes())
        except ET.ParseError as exc:
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, path)
            raise ConfigParseError(
                path, f"post-write XML parse failed: {exc}",
            ) from exc
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)
        if backup_path and not keep_backup and backup_path.exists():
            try:
                backup_path.unlink()
            except OSError:
                logging.getLogger("media_stack").debug("[DEBUG] Swallowed exception", exc_info=True)


def set_or_create_child(
    parent: ET.Element, tag: str, value: str,
) -> bool:
    """Set ``parent/tag``'s text to ``value``. Create the child if
    missing. Returns True if anything changed."""
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag)
        child.text = value
        return True
    if (child.text or "") != value:
        child.text = value
        return True
    return False
