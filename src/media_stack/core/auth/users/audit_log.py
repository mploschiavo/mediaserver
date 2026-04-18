"""Append-only hash-chained audit log for user-management actions."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from media_stack.core.auth.users.models import AuditEntry

_DEFAULT_RECENT_LIMIT = 100


class AuditLog:

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _canonical(self, entry: dict[str, Any]) -> str:
        return json.dumps(entry, sort_keys=True, separators=(",", ":"))

    def _last_hash(self) -> str:
        if not self._path.is_file():
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
        return entry

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
