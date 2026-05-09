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
import sys
import time
import threading
from pathlib import Path
from typing import Any


_LOCK = threading.Lock()

# Minimum cadence — exposed as a constant so the UI editor and the
# server-side guard rail agree.
MIN_INTERVAL_SECONDS = 60

# Default cooldown applied when a persisted schedule doesn't carry
# ``interval_seconds`` (defensive; the writer always emits one).
_DEFAULT_INTERVAL_SECONDS = 3600

_DEFAULT_CONFIG_ROOT = "/srv-config"


class SchedulerService:
    """Persistent scheduled task management.

    Single shared filesystem-backed catalog; concurrent reads/writes
    serialize via the module-level ``_LOCK``.
    """

    def normalize(self, schedule: dict[str, Any]) -> dict[str, Any]:
        """Backfill missing fields on a schedule loaded from disk so
        callers never have to defend against absent keys.

        Currently only ``enabled`` (added v1.0.279) needs backfill —
        older persisted entries default to ``True`` so an upgrade
        doesn't pause every existing schedule.
        """
        if "enabled" not in schedule:
            return {**schedule, "enabled": True}
        return schedule

    def get_schedules(self) -> dict[str, Any]:
        """Return all configured schedules."""
        with _LOCK:
            schedules = [
                self.normalize(s)
                for s in sys.modules[__name__]._load_schedules()
            ]
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
            schedules = sys.modules[__name__]._load_schedules()
            schedules.append(schedule)
            sys.modules[__name__]._save_schedules(schedules)
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
            schedules = sys.modules[__name__]._load_schedules()
            for i, s in enumerate(schedules):
                if s.get("id") != schedule_id:
                    continue
                updated = self.normalize(dict(s))
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
                sys.modules[__name__]._save_schedules(schedules)
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
            schedules = sys.modules[__name__]._load_schedules()
            before = len(schedules)
            schedules = [
                s for s in schedules if s.get("id") != schedule_id
            ]
            if len(schedules) == before:
                return {"error": f"Schedule {schedule_id} not found"}
            sys.modules[__name__]._save_schedules(schedules)
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
            schedules = sys.modules[__name__]._load_schedules()
            changed = False
            for s in schedules:
                normalized = self.normalize(s)
                if not normalized.get("enabled", True):
                    continue
                last_run = normalized.get("last_run", 0)
                interval = normalized.get(
                    "interval_seconds", _DEFAULT_INTERVAL_SECONDS,
                )
                if (now - last_run) >= interval:
                    due.append(normalized)
                    s["last_run"] = now
                    changed = True
            if changed:
                sys.modules[__name__]._save_schedules(schedules)
        return due

    def schedules_path(self) -> Path:
        """Return the on-disk schedules.json path.

        Re-resolved on every call so tests that flip ``CONFIG_ROOT``
        via ``monkeypatch.setenv`` see the override immediately. The
        mkdir + path-join cost is negligible relative to the
        surrounding JSON read/write.
        """
        config_root = Path(
            os.environ.get("CONFIG_ROOT", _DEFAULT_CONFIG_ROOT),
        )
        ctrl_dir = config_root / ".controller"
        ctrl_dir.mkdir(parents=True, exist_ok=True)
        return ctrl_dir / "schedules.json"

    def load_schedules(self) -> list[dict[str, Any]]:
        """Read the persisted schedules list. Returns ``[]`` on a
        missing file or unreadable JSON — the catalog is best-effort
        and the dispatcher must never crash because of disk damage.
        """
        path = sys.modules[__name__]._schedules_path()
        if not path.is_file():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return []

    def save_schedules(self, schedules: list[dict[str, Any]]) -> None:
        """Persist the schedules list to disk."""
        path = sys.modules[__name__]._schedules_path()
        path.write_text(
            json.dumps(schedules, indent=2), encoding="utf-8",
        )


_INSTANCE = SchedulerService()

# Backward-compat module-level aliases — every public + underscore
# name on ``_INSTANCE`` is rebound here so callers that still do
# ``scheduler.add_schedule(...)`` keep working, and so tests can
# ``mock.patch("media_stack.api.services.scheduler.<name>")``. The
# instance methods that go through ``sys.modules[__name__]._load_schedules``
# pick up those patches because the indirection re-reads the module
# attribute on every call.
normalize = _INSTANCE.normalize
get_schedules = _INSTANCE.get_schedules
add_schedule = _INSTANCE.add_schedule
update_schedule = _INSTANCE.update_schedule
set_schedule_enabled = _INSTANCE.set_schedule_enabled
remove_schedule = _INSTANCE.remove_schedule
get_due_actions = _INSTANCE.get_due_actions
_normalize = _INSTANCE.normalize
_schedules_path = _INSTANCE.schedules_path
_load_schedules = _INSTANCE.load_schedules
_save_schedules = _INSTANCE.save_schedules


__all__ = [
    "MIN_INTERVAL_SECONDS",
    "SchedulerService",
    "add_schedule",
    "get_due_actions",
    "get_schedules",
    "normalize",
    "remove_schedule",
    "set_schedule_enabled",
    "update_schedule",
]
