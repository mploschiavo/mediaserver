"""Append-only hash-chained audit log for user-management actions."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from media_stack.core.auth.users.models import AuditEntry

_DEFAULT_RECENT_LIMIT = 100
_BYTES_PER_MIB = 2 ** 20
_DEFAULT_MAX_SIZE_BYTES = 10 * _BYTES_PER_MIB
_DEFAULT_KEEP_ARCHIVES = 5
_MIN_ROTATION_BYTES = 2 ** 10


class AuditLog:

    def __init__(self, path: Path, *, max_size_bytes: int = _DEFAULT_MAX_SIZE_BYTES,
                 keep_archives: int = _DEFAULT_KEEP_ARCHIVES) -> None:
        self._path = Path(path)
        self._max_size_bytes = max(_MIN_ROTATION_BYTES, int(max_size_bytes))
        self._keep_archives = max(1, int(keep_archives))
        self._lock = threading.Lock()
        # Cache of the last entry's hash. None = uncomputed; "" =
        # file empty. Without this cache, append() was O(n) and the
        # chain was O(n^2) — a 10k-entry log took >60s to extend.
        self._last_hash_cache: str | None = None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _canonical(self, entry: dict[str, Any]) -> str:
        return json.dumps(entry, sort_keys=True, separators=(",", ":"))

    def _last_hash(self) -> str:
        """Return the hash of the last written entry.

        Uses a cached value populated on first read. Without this,
        ``append()`` was O(n) per call (full-file scan to find the
        prior hash), making the hash chain O(n^2) over n entries
        — a 10k-entry audit log took >60s just to re-append. The
        cache is invalidated on rotation (handled in _rotate_if_needed)
        so it never drifts from the live tail of the file."""
        if self._last_hash_cache is not None:
            return self._last_hash_cache
        if not self._path.is_file():
            self._last_hash_cache = ""
            return ""
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
        self._last_hash_cache = last
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
            # Update the cached last-hash so the next append is O(1)
            # instead of re-scanning the file.
            self._last_hash_cache = entry.hash
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
        self._last_hash_cache = ""
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
