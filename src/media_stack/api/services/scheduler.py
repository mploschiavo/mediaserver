"""Persistent scheduled tasks — server-side cron-like action scheduling.

Schedules are stored in a JSON file under CONFIG_ROOT/.controller/schedules.json
and survive container restarts. The dispatch loop in controller_serve.py
checks for due tasks on each action queue cycle.

Schedule shape (forward-compatible):

    {
      "id": int,                    # ms-precision creation time
      "action": str,                # the job/action to fire
      "interval_seconds": int,      # cadence (minimum 60s)
      "label": str,                 # operator-facing label
      "created_at": float,          # epoch seconds
      "last_run": float,            # epoch seconds (0 = never)
      "enabled": bool,              # added in v1.0.279 — paused
                                    # schedules don't fire but stay
                                    # in the catalog
    }

Older schedules persisted before ``enabled`` was added are silently
treated as ``enabled=True`` so a fresh deploy doesn't have to migrate
the file.
"""

from __future__ import annotations

import json
import os
import time
import threading
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()

# Minimum cadence — exposed as a constant so the UI editor and the
# server-side guard rail agree.
MIN_INTERVAL_SECONDS = 60


def _normalize(schedule: dict[str, Any]) -> dict[str, Any]:
    """Backfill missing fields on a schedule loaded from disk so
    callers never have to defend against absent keys.

    Currently only ``enabled`` (added v1.0.279) needs backfill —
    older persisted entries default to ``True`` so an upgrade
    doesn't pause every existing schedule.
    """
    if "enabled" not in schedule:
        schedule = {**schedule, "enabled": True}
    return schedule


class SchedulerService:
    """Persistent scheduled task management."""

    def get_schedules(self) -> dict[str, Any]:
        """Return all configured schedules."""
        with _LOCK:
            schedules = [_normalize(s) for s in _load_schedules()]
        return {"schedules": schedules, "count": len(schedules)}

    def add_schedule(
        self,
        action: str,
        interval_seconds: int,
        label: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Add a new recurring schedule."""
        if not action:
            return {"error": "action is required"}
        if interval_seconds < MIN_INTERVAL_SECONDS:
            return {
                "error": (
                    f"interval must be at least {MIN_INTERVAL_SECONDS}"
                    " seconds"
                ),
            }
        schedule = {
            "id": int(time.time() * 1000),
            "action": action,
            "interval_seconds": interval_seconds,
            "label": label or f"{action} every {interval_seconds}s",
            "created_at": time.time(),
            "last_run": 0,
            "enabled": bool(enabled),
        }
        with _LOCK:
            schedules = _load_schedules()
            schedules.append(schedule)
            _save_schedules(schedules)
        return {"status": "created", "schedule": schedule}

    def update_schedule(
        self,
        schedule_id: int,
        *,
        action: str | None = None,
        interval_seconds: int | None = None,
        label: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Patch an existing schedule. Only the supplied fields are
        modified; ``None`` means leave the prior value alone.

        ``id`` / ``created_at`` / ``last_run`` are never editable —
        the first two are immutable identity, the third is owned by
        ``get_due_actions``.
        """
        if (
            interval_seconds is not None
            and interval_seconds < MIN_INTERVAL_SECONDS
        ):
            return {
                "error": (
                    f"interval must be at least {MIN_INTERVAL_SECONDS}"
                    " seconds"
                ),
            }
        with _LOCK:
            schedules = _load_schedules()
            for i, s in enumerate(schedules):
                if s.get("id") != schedule_id:
                    continue
                updated = _normalize(dict(s))
                if action is not None:
                    if not action:
                        return {"error": "action cannot be empty"}
                    updated["action"] = action
                if interval_seconds is not None:
                    updated["interval_seconds"] = interval_seconds
                if label is not None:
                    updated["label"] = label
                if enabled is not None:
                    updated["enabled"] = bool(enabled)
                schedules[i] = updated
                _save_schedules(schedules)
                return {"status": "updated", "schedule": updated}
        return {"error": f"Schedule {schedule_id} not found"}

    def set_schedule_enabled(
        self, schedule_id: int, *, enabled: bool,
    ) -> dict[str, Any]:
        """Convenience wrapper around ``update_schedule`` exposed as
        the ``pause`` / ``resume`` HTTP handlers."""
        return self.update_schedule(schedule_id, enabled=enabled)

    def remove_schedule(self, schedule_id: int) -> dict[str, Any]:
        """Remove a schedule by ID."""
        with _LOCK:
            schedules = _load_schedules()
            before = len(schedules)
            schedules = [s for s in schedules if s.get("id") != schedule_id]
            if len(schedules) == before:
                return {"error": f"Schedule {schedule_id} not found"}
            _save_schedules(schedules)
        return {"status": "removed", "schedule_id": schedule_id}

    def get_due_actions(self) -> list[dict[str, Any]]:
        """Return schedules that are due to run, and update their
        last_run timestamp.

        Paused schedules (``enabled=False``) are skipped — they
        remain in the catalog but the dispatcher never sees them.
        """
        now = time.time()
        due: list[dict[str, Any]] = []
        with _LOCK:
            schedules = _load_schedules()
            changed = False
            for s in schedules:
                normalized = _normalize(s)
                if not normalized.get("enabled", True):
                    continue
                last_run = normalized.get("last_run", 0)
                interval = normalized.get("interval_seconds", 3600)
                if (now - last_run) >= interval:
                    due.append(normalized)
                    s["last_run"] = now
                    changed = True
            if changed:
                _save_schedules(schedules)
        return due


    @staticmethod
    def _schedules_path() -> Path:
        # Re-resolved on every call so tests that flip ``CONFIG_ROOT``
        # via ``monkeypatch.setenv`` see the override immediately. The
        # mkdir + path-join cost is negligible relative to the
        # surrounding JSON read/write.
        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        ctrl_dir = config_root / ".controller"
        ctrl_dir.mkdir(parents=True, exist_ok=True)
        return ctrl_dir / "schedules.json"

    @staticmethod
    def _load_schedules() -> list[dict[str, Any]]:
        path = _schedules_path()
        if not path.is_file():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    @staticmethod
    def _save_schedules(schedules: list[dict[str, Any]]) -> None:
        path = _schedules_path()
        path.write_text(json.dumps(schedules, indent=2), encoding="utf-8")


_instance = SchedulerService()

# Backward compat — callers use module-level functions
get_schedules = _instance.get_schedules
add_schedule = _instance.add_schedule
update_schedule = _instance.update_schedule
set_schedule_enabled = _instance.set_schedule_enabled
remove_schedule = _instance.remove_schedule
get_due_actions = _instance.get_due_actions
_schedules_path = _instance._schedules_path
_load_schedules = _instance._load_schedules
_save_schedules = _instance._save_schedules
