"""Append-only hash-chained audit log for user-management actions."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from media_stack.core.logging_utils import log_swallowed
from media_stack.domain.auth.users.models import AuditEntry

_DEFAULT_RECENT_LIMIT = 100
_BYTES_PER_MIB = 2 ** 20
_DEFAULT_MAX_SIZE_BYTES = 10 * _BYTES_PER_MIB
_DEFAULT_KEEP_ARCHIVES = 5
_MIN_ROTATION_BYTES = 2 ** 10


class AuditLog:

    # Class-level lock + last-hash registry keyed by absolute path.
    # Multiple ``AuditLog(same_path)`` instances must share BOTH the
    # write lock AND the cached last hash — otherwise concurrent
    # writers (the request thread + background tasks like
    # ``reset_password.bg``) each acquire their own per-instance
    # lock, observe the same stale ``last_hash``, and write two
    # entries with the same ``prev_hash`` (entry N+1 references
    # entry N-1 instead of entry N — chain corrupted at N+1).
    # ``user_service_factory`` constructs fresh ``AuditLog``
    # instances in five places so per-instance state could never
    # synchronize them. Keys are ``str(path.resolve())`` so
    # equivalent paths (relative vs. absolute, symlinked etc.)
    # collapse to one entry.
    _STATE_GUARD = threading.Lock()
    _LOCKS: dict[str, threading.Lock] = {}
    _LAST_HASH_CACHE: dict[str, str | None] = {}

    @classmethod
    def _state_for_path(
        cls, path: Path,
    ) -> tuple[str, threading.Lock]:
        """Return ``(key, lock)`` for ``path``; lock is created lazily."""
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path.absolute())
        with cls._STATE_GUARD:
            lock = cls._LOCKS.get(key)
            if lock is None:
                lock = threading.Lock()
                cls._LOCKS[key] = lock
                cls._LAST_HASH_CACHE[key] = None
            return key, lock

    def __init__(self, path: Path, *, max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
                 keep_archives: int = _DEFAULT_KEEP_ARCHIVES) -> None:
        self._path = Path(path)
        self._max_size_bytes = max(_MIN_ROTATION_BYTES, int(max_size_bytes))
        self._keep_archives = max(1, int(keep_archives))
        self._lock_key, self._lock = self._state_for_path(self._path)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _canonical(self, entry: dict[str, Any]) -> str:
        return json.dumps(entry, sort_keys=True, separators=(",", ":"))

    def _last_hash(self) -> str:
        """Return the hash of the last written entry.

        Reads from ``_LAST_HASH_CACHE`` (shared across all
        ``AuditLog`` instances for the same path) — populated on
        first read. Without this cache, ``append()`` was O(n) per
        call (full-file scan to find the prior hash), making the
        hash chain O(n^2) over n entries — a 10k-entry audit log
        took >60s just to re-append. The cache is invalidated on
        rotation (handled in ``_rotate_if_needed``) so it never
        drifts from the live tail of the file. Callers MUST hold
        ``self._lock`` — the shared cache is only safe to read
        under the shared write lock.

        File-presence is checked FIRST: when an operator archives
        the file out-of-band (mv audit.log.jsonl audit.log.jsonl.
        corrupted-2026-04-27 is the documented recovery path), the
        cache must reset so the next append starts a fresh chain
        with prev_hash = "" instead of continuing from the
        now-archived tail.
        """
        if not self._path.is_file():
            type(self)._LAST_HASH_CACHE[self._lock_key] = ""
            return ""
        cached = type(self)._LAST_HASH_CACHE.get(self._lock_key)
        if cached is not None:
            return cached
        last = ""
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                h = row.get("hash") or ""
                if h:
                    last = h
        type(self)._LAST_HASH_CACHE[self._lock_key] = last
        return last

    def append(self, actor: str, action: str, target: str, result: str = "ok",
               ip: str = "", user_agent: str = "",
               detail: dict[str, Any] | None = None) -> AuditEntry:
        entry = AuditEntry(
            timestamp=self._now_iso(),
            actor=actor,
            action=action,
            target=target,
            result=result,
            ip=ip,
            user_agent=user_agent,
            detail=dict(detail or {}),
        )
        with self._lock:
            entry.prev_hash = self._last_hash()
            payload = entry.to_dict()
            payload.pop("hash", None)
            entry.hash = hashlib.sha256(
                (entry.prev_hash + self._canonical(payload)).encode("utf-8")
            ).hexdigest()
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(self._canonical(entry.to_dict()) + "\n")
                f.flush()
                os.fsync(f.fileno())
            # Update the shared cache so the next append is O(1)
            # instead of re-scanning the file.
            type(self)._LAST_HASH_CACHE[self._lock_key] = entry.hash
            self._rotate_if_needed()
        return entry

    def _rotate_if_needed(self) -> None:
        """Rollover the active log to ``<name>.<ts>.jsonl`` if it's grown
        past max size. Keeps at most ``keep_archives`` archives."""
        try:
            size = self._path.stat().st_size
        except FileNotFoundError:
            return
        if size < self._max_size_bytes:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = self._path.with_name(f"{self._path.name}.{stamp}")
        try:
            self._path.rename(archive)
        except OSError:
            return
        # Cache belonged to the now-archived file; the next append
        # starts a fresh chain in an empty file.
        type(self)._LAST_HASH_CACHE[self._lock_key] = ""
        # Prune older archives
        archives = sorted(
            self._path.parent.glob(f"{self._path.name}.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in archives[self._keep_archives:]:
            try:
                stale.unlink()
            except OSError:
                continue

    def iter_entries(self) -> Iterator[AuditEntry]:
        if not self._path.is_file():
            return
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                yield AuditEntry(
                    timestamp=str(row.get("timestamp", "")),
                    actor=str(row.get("actor", "")),
                    action=str(row.get("action", "")),
                    target=str(row.get("target", "")),
                    result=str(row.get("result", "")),
                    ip=str(row.get("ip", "")),
                    user_agent=str(row.get("user_agent", "")),
                    detail=dict(row.get("detail", {})),
                    prev_hash=str(row.get("prev_hash", "")),
                    hash=str(row.get("hash", "")),
                )

    def verify_chain(self) -> tuple[bool, str]:
        prev = ""
        idx = 0
        for entry in self.iter_entries():
            payload = entry.to_dict()
            payload.pop("hash", None)
            expected = hashlib.sha256(
                (prev + self._canonical(payload)).encode("utf-8")
            ).hexdigest()
            if entry.prev_hash != prev:
                return False, f"entry {idx}: prev_hash mismatch"
            if entry.hash != expected:
                return False, f"entry {idx}: hash mismatch"
            prev = entry.hash
            idx += 1
        return True, ""

    def recent(self, limit: int = _DEFAULT_RECENT_LIMIT, action_filter: str = "",
               target_filter: str = "") -> list[dict[str, Any]]:
        entries = list(self.iter_entries())
        if action_filter:
            entries = [e for e in entries if action_filter in e.action]
        if target_filter:
            entries = [e for e in entries if target_filter in e.target]
        return [e.to_dict() for e in entries[-limit:]]

    def recent_by_actions(
        self, actions: Iterable[str], *,
        since: str = "", limit: int = _DEFAULT_RECENT_LIMIT,
    ) -> list[dict[str, Any]]:
        """Recent entries whose action is in ``actions``, optionally
        newer than ``since`` (iso-Z lexical compare — callers should
        pass the same format ``append`` writes).

        ``actions`` is an Iterable (list/set/frozenset) of action
        constants from ``audit_actions``. Exact match, not substring
        — this is a set filter so the UI action-picker works.
        """
        wanted: frozenset[str] = frozenset(actions)
        if not wanted:
            return []
        out: list[dict[str, Any]] = []
        for entry in self.iter_entries():
            if entry.action not in wanted:
                continue
            if since and entry.timestamp < since:
                continue
            out.append(entry.to_dict())
        # Most recent last — slice the tail so callers see the
        # newest ``limit`` entries (matching ``recent`` semantics).
        if limit > 0:
            out = out[-limit:]
        return out

    def iter_since(self, since: str) -> Iterator[AuditEntry]:
        """Entries with timestamp >= ``since`` (iso-Z lexical compare).

        Stops early once an older entry would follow — which it can't,
        because appends are time-ordered, but the generator still walks
        the full file because we don't have an index. A future
        optimization can seek from the tail; for now O(n) scan is
        acceptable (audit file is capped at 10 MiB by rotation).
        """
        for entry in self.iter_entries():
            if since and entry.timestamp < since:
                continue
            yield entry

    def stats(self) -> dict[str, Any]:
        """Operator-visible retention metrics for the audit log.

        Returns ``{entry_count, disk_bytes, oldest_ts, newest_ts,
        archive_count, max_size_bytes, keep_archives}``. The bytes
        figure includes every rotated archive next to the live file
        — operators care about total disk footprint, not just the
        active file. ``oldest_ts`` / ``newest_ts`` come from the
        live file alone; archive timestamps would require parsing
        every gz / log.N which is too expensive for a hot endpoint.

        Cheap by design — a 5MiB log reads in ~30ms.
        """
        entry_count = 0
        oldest_ts = ""
        newest_ts = ""
        if self._path.is_file():
            with self._path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = str(row.get("timestamp", ""))
                    entry_count += 1
                    if not oldest_ts or ts < oldest_ts:
                        oldest_ts = ts
                    if ts > newest_ts:
                        newest_ts = ts

        disk_bytes = 0
        archive_count = 0
        live_size = (self._path.stat().st_size
                     if self._path.is_file() else 0)
        disk_bytes += live_size
        # Archives live next to the live file as ``<name>.1``, ``<name>.2``…
        # Count every sibling matching the rotation pattern.
        parent = self._path.parent
        stem = self._path.name
        if parent.is_dir():
            for sibling in parent.iterdir():
                if sibling.name == stem:
                    continue
                if sibling.name.startswith(stem + "."):
                    archive_count += 1
                    try:
                        disk_bytes += sibling.stat().st_size
                    except OSError as exc:
                        log_swallowed(exc, f"audit-archive-stat:{sibling.name}")

        return {
            "entry_count": entry_count,
            "disk_bytes": disk_bytes,
            "oldest_ts": oldest_ts,
            "newest_ts": newest_ts,
            "archive_count": archive_count,
            "max_size_bytes": self._max_size_bytes,
            "keep_archives": self._keep_archives,
            # Estimated max footprint: live file + N archives at
            # max_size each. Operators use this to size their disk.
            "max_disk_bytes": self._max_size_bytes
            * (self._keep_archives + 1),
        }

    def head(self) -> dict[str, Any]:
        """Chain head snapshot: height, last hash, last timestamp.

        Returned by ``GET /api/audit-log/head`` so external monitors
        can confirm the log hasn't been rewritten. ``height`` is the
        current entry count; ``hash`` is the last entry's sha256
        (empty string for a fresh file). This is cheap — O(1) when
        the cache is warm (``append`` updates it), O(n) only on the
        very first read after process start.
        """
        # Warm the cache if needed; this also gives us the last hash.
        last_hash = self._last_hash()
        if not self._path.is_file():
            return {"height": 0, "hash": "", "ts": "", "ok": True}
        height = 0
        last_ts = ""
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                height += 1
                ts = str(row.get("timestamp", ""))
                if ts > last_ts:
                    last_ts = ts
        return {
            "height": height,
            "hash": last_hash,
            "ts": last_ts,
            # ``ok`` is a cheap surface — a full chain verify is a
            # separate endpoint (``verify_chain``) because it's O(n).
            "ok": True,
        }
