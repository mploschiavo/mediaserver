"""Thread-safe bootstrap run state shared between API server and runner."""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class ActionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class ActionRecord:
    """Record of an action execution (in-progress or completed)."""

    id: str
    name: str
    status: ActionStatus = ActionStatus.PENDING
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    overrides: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 600

    def start(self) -> None:
        self.status = ActionStatus.RUNNING
        self.started_at = time.time()

    def finish(self, error: str | None = None) -> None:
        self.completed_at = time.time()
        if error:
            self.status = ActionStatus.ERROR
            self.error = error
        else:
            self.status = ActionStatus.COMPLETE

    def cancel(self) -> None:
        self.completed_at = time.time()
        self.status = ActionStatus.CANCELLED
        self.error = "cancelled by user"

    def mark_timeout(self) -> None:
        self.completed_at = time.time()
        self.status = ActionStatus.TIMEOUT
        self.error = f"exceeded {self.timeout_seconds}s timeout"

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at or time.time()
        return round(end - self.started_at, 1)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            ActionStatus.COMPLETE,
            ActionStatus.ERROR,
            ActionStatus.CANCELLED,
            ActionStatus.TIMEOUT,
        )

    @property
    def is_timed_out(self) -> bool:
        if self.status != ActionStatus.RUNNING or self.started_at is None:
            return False
        return (time.time() - self.started_at) > self.timeout_seconds

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "overrides": dict(self.overrides),
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class BootstrapState:
    """Mutable state shared between the HTTP API thread and the bootstrap runner thread."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _action_counter: int = field(default=0, repr=False)

    # Legacy fields — kept for backward compatibility with host-side polling.
    phase: str = "idle"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    phases_completed: list[str] = field(default_factory=list)
    preflight_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    app_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    run_overrides: dict[str, Any] = field(default_factory=dict)

    # Action tracking.
    initial_bootstrap_done: bool = False
    current_action: ActionRecord | None = None
    action_history: list[ActionRecord] = field(default_factory=list)

    # --- legacy single-run interface (used by first bootstrap via _run_serve) ---

    def start(self) -> None:
        with self._lock:
            self.phase = "running"
            self.started_at = time.time()
            self.completed_at = None
            self.error = None
            self.phases_completed = []

    def complete_phase(self, phase_name: str) -> None:
        with self._lock:
            self.phases_completed.append(phase_name)

    def finish(self, error: str | None = None) -> None:
        with self._lock:
            self.completed_at = time.time()
            self.phase = "error" if error else "complete"
            self.error = error
            if not error:
                self.initial_bootstrap_done = True

    def record_preflight(self, name: str, result: dict[str, Any]) -> None:
        with self._lock:
            self.preflight_results[name] = dict(result)

    def record_app_status(self, app_name: str, status: str, **details: Any) -> None:
        with self._lock:
            self.app_status[app_name] = {"status": status, **details}

    @property
    def is_running(self) -> bool:
        return self.phase == "running"

    @property
    def is_complete(self) -> bool:
        return self.phase in ("complete", "error")

    # --- action lifecycle ---

    def start_action(
        self,
        action_name: str,
        overrides: dict[str, Any] | None = None,
        timeout_seconds: int = 600,
    ) -> ActionRecord:
        with self._lock:
            self._action_counter += 1
            action = ActionRecord(
                id=f"{action_name}-{self._action_counter}",
                name=action_name,
                overrides=dict(overrides or {}),
                timeout_seconds=timeout_seconds,
            )
            action.start()
            self.current_action = action
            self._cancel_event.clear()
            # Update legacy fields for backward-compatible polling.
            self.phase = "running"
            self.error = None
            return action

    def finish_action(self, error: str | None = None) -> None:
        with self._lock:
            if self.current_action and not self.current_action.is_terminal:
                self.current_action.finish(error)
                self.action_history.append(self.current_action)
            self.current_action = None
            self._cancel_event.clear()
            # Update legacy fields.
            self.completed_at = time.time()
            self.phase = "error" if error else "complete"
            self.error = error

    def cancel_action(self) -> bool:
        """Request cancellation of the current action. Returns True if an action was running."""
        with self._lock:
            if self.current_action and not self.current_action.is_terminal:
                self._cancel_event.set()
                return True
            return False

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    @property
    def action_running(self) -> bool:
        with self._lock:
            return self.current_action is not None and not self.current_action.is_terminal

    def get_action(self, action_id: str) -> ActionRecord | None:
        with self._lock:
            if self.current_action and self.current_action.id == action_id:
                return self.current_action
            for record in reversed(self.action_history):
                if record.id == action_id:
                    return record
            return None

    # --- serialization ---

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            elapsed = None
            if self.started_at is not None:
                end = self.completed_at or time.time()
                elapsed = round(end - self.started_at, 1)
            return {
                "phase": self.phase,
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "elapsed_seconds": elapsed,
                "error": self.error,
                "phases_completed": list(self.phases_completed),
                "preflight_results": dict(self.preflight_results),
                "app_status": dict(self.app_status),
                "run_overrides": dict(self.run_overrides),
                "initial_bootstrap_done": self.initial_bootstrap_done,
                "current_action": self.current_action.to_dict() if self.current_action else None,
                "action_history": [a.to_dict() for a in self.action_history],
            }
