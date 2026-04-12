"""Persistent scheduled tasks — server-side cron-like action scheduling.

Schedules are stored in a JSON file under CONFIG_ROOT/.controller/schedules.json
and survive container restarts. The dispatch loop in controller_serve.py
checks for due tasks on each action queue cycle.
"""

from __future__ import annotations

import json
import os
import time
import threading
from pathlib import Path
from typing import Any, Callable


_SCHEDULES_FILE = None
_LOCK = threading.Lock()








class SchedulerService:
    """Persistent scheduled task management."""

    def get_schedules(self) -> dict[str, Any]:
        """Return all configured schedules."""
        with _LOCK:
            schedules = _load_schedules()
        return {"schedules": schedules, "count": len(schedules)}

    def add_schedule(self, action: str, interval_seconds: int, label: str = "") -> dict[str, Any]:
        """Add a new recurring schedule."""
        if not action:
            return {"error": "action is required"}
        if interval_seconds < 60:
            return {"error": "interval must be at least 60 seconds"}
        schedule = {
            "id": int(time.time() * 1000),
            "action": action,
            "interval_seconds": interval_seconds,
            "label": label or f"{action} every {interval_seconds}s",
            "created_at": time.time(),
            "last_run": 0,
        }
        with _LOCK:
            schedules = _load_schedules()
            schedules.append(schedule)
            _save_schedules(schedules)
        return {"status": "created", "schedule": schedule}

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
        """Return schedules that are due to run, and update their last_run timestamp."""
        now = time.time()
        due: list[dict[str, Any]] = []
        with _LOCK:
            schedules = _load_schedules()
            changed = False
            for s in schedules:
                last_run = s.get("last_run", 0)
                interval = s.get("interval_seconds", 3600)
                if (now - last_run) >= interval:
                    due.append(s)
                    s["last_run"] = now
                    changed = True
            if changed:
                _save_schedules(schedules)
        return due


    @staticmethod
    def _schedules_path() -> Path:
        global _SCHEDULES_FILE
        if _SCHEDULES_FILE is None:
            config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
            ctrl_dir = config_root / ".controller"
            ctrl_dir.mkdir(parents=True, exist_ok=True)
            _SCHEDULES_FILE = ctrl_dir / "schedules.json"
        return _SCHEDULES_FILE

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
remove_schedule = _instance.remove_schedule
get_due_actions = _instance.get_due_actions
_schedules_path = _instance._schedules_path
_load_schedules = _instance._load_schedules
_save_schedules = _instance._save_schedules
