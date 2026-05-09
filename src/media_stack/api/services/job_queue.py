"""Persistent operator-managed job queue.

Distinct from ``scheduler.py`` (cadence-based recurring tasks) and
from ``JobRunner.dispatched`` (in-process bookkeeping for the
currently-firing batch): this queue holds the *pending* work the
operator explicitly asked for via the Jobs page. Each entry is a
{job_name, source, scheduled_at} record persisted to
``$CONFIG_ROOT/.controller/queue.json`` so it survives controller
restarts.

For v1.0.280 the queue is read/CRUD-only — the JobRunner doesn't
yet drain it. Operators see what they queued via the Jobs page's
QueueCard, can reorder via ``↑/↓``, and can remove via ``×``. The
auto-dispatch hookup is tracked in ``project_jobs_polish_deferred``
because wiring it requires a small ``JobRunner`` refactor (current
runner is a one-shot batch dispatcher; queue draining means a
persistent loop). This phase ships the operator surface.

Storage shape:

    [
      {
        "id": int,                 # ms-precision creation time
        "job_name": str,           # action/job to fire
        "source": str,             # "manual" / "config-save" / etc.
        "scheduled_at": float,     # epoch — informational only;
                                   # ``None``/``0`` = run ASAP
        "enqueued_at": float,      # epoch the operator added it
        "label": str,              # operator-facing display
      },
      ...
    ]

Order is preserved: index 0 = head of queue.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


class JobQueueRepository:
    """Persistent operator-managed job queue backed by a JSON file.

    Methods are plain instance methods so callers can swap in a
    test double; the module-level ``_REPOSITORY`` singleton (and the
    ``get_queue``/``enqueue``/... aliases below) preserve the legacy
    function-style import surface used by route handlers.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Monotonic counter that bumps the millisecond clock when two
        # enqueues land inside the same tick. Using ``time.time() * 1000``
        # alone produces colliding IDs under fast test fixtures (and on
        # operators clicking Run-now twice in 50ms).
        self._last_id = 0

    def _next_id(self) -> int:
        candidate = int(time.time() * 1000)
        if candidate <= self._last_id:
            candidate = self._last_id + 1
        self._last_id = candidate
        return candidate

    def _queue_path(self) -> Path:
        """Re-resolved on every call so tests overriding ``CONFIG_ROOT``
        via ``monkeypatch.setenv`` see the change immediately."""
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        ctrl_dir = config_root / ".controller"
        ctrl_dir.mkdir(parents=True, exist_ok=True)
        return ctrl_dir / "queue.json"

    def _load(self) -> list[dict[str, Any]]:
        path = self._queue_path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save(self, entries: list[dict[str, Any]]) -> None:
        path = self._queue_path()
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")

    def get_queue(self) -> dict[str, Any]:
        """Return the current queue snapshot.

        Wrapped in ``{"queue": [...], "count": N}`` so the response shape
        matches the rest of the controller's collection endpoints
        (schedules, runs, jobs).
        """
        with self._lock:
            entries = self._load()
        return {"queue": entries, "count": len(entries)}

    def enqueue(
        self,
        job_name: str,
        *,
        source: str = "manual",
        scheduled_at: float = 0,
        label: str = "",
    ) -> dict[str, Any]:
        """Append a new entry to the tail of the queue.

        Returns the persisted entry on success, ``{"error": ...}`` on
        validation failure. ``scheduled_at=0`` means "run ASAP" — the
        field is informational only since the dispatcher integration
        isn't wired yet.
        """
        if not job_name or not job_name.strip():
            return {"error": "job_name is required"}
        with self._lock:
            entry_id = self._next_id()
        entry = {
            "id": entry_id,
            "job_name": job_name.strip(),
            "source": source or "manual",
            "scheduled_at": float(scheduled_at) if scheduled_at else 0,
            "enqueued_at": time.time(),
            "label": label or job_name.strip(),
        }
        with self._lock:
            entries = self._load()
            entries.append(entry)
            self._save(entries)
        return {"status": "queued", "entry": entry}

    def remove_entry(self, entry_id: int) -> dict[str, Any]:
        """Drop a queued entry by id."""
        with self._lock:
            entries = self._load()
            before = len(entries)
            entries = [e for e in entries if e.get("id") != entry_id]
            if len(entries) == before:
                return {"error": f"queue entry {entry_id} not found"}
            self._save(entries)
        return {"status": "removed", "id": entry_id}

    def reorder_entry(
        self,
        entry_id: int,
        *,
        direction: str | None = None,
        position: int | None = None,
    ) -> dict[str, Any]:
        """Move a queued entry up/down by one slot or to an absolute index.

        Exactly one of ``direction`` (``"up"``/``"down"``) or ``position``
        (0-based index) must be supplied. Out-of-bounds moves are
        clamped — moving the head ``up`` is a no-op rather than an
        error so the UI can wire the buttons unconditionally.
        """
        if direction is None and position is None:
            return {"error": "direction or position is required"}
        if direction is not None and direction not in ("up", "down"):
            return {"error": "direction must be 'up' or 'down'"}
        with self._lock:
            entries = self._load()
            idx = next(
                (i for i, e in enumerate(entries) if e.get("id") == entry_id),
                -1,
            )
            if idx < 0:
                return {"error": f"queue entry {entry_id} not found"}
            target: int
            if position is not None:
                target = max(0, min(len(entries) - 1, int(position)))
            elif direction == "up":
                target = max(0, idx - 1)
            else:
                target = min(len(entries) - 1, idx + 1)
            if target == idx:
                return {"status": "noop", "id": entry_id, "position": idx}
            entry = entries.pop(idx)
            entries.insert(target, entry)
            self._save(entries)
            return {"status": "reordered", "id": entry_id, "position": target}

    def clear_queue(self) -> dict[str, Any]:
        """Wipe every entry — admin escape hatch for a stuck queue."""
        with self._lock:
            entries = self._load()
            count = len(entries)
            self._save([])
        return {"status": "cleared", "count": count}


_REPOSITORY = JobQueueRepository()

# Legacy function-style aliases so existing imports keep working.
get_queue = _REPOSITORY.get_queue
enqueue = _REPOSITORY.enqueue
remove_entry = _REPOSITORY.remove_entry
reorder_entry = _REPOSITORY.reorder_entry
clear_queue = _REPOSITORY.clear_queue
