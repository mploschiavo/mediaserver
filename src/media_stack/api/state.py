"""Thread-safe bootstrap run state shared between API server and runner."""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import collections
import enum
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import logging


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
    triggered_by: str = "system"

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
            "triggered_by": self.triggered_by,
        }


@dataclass
class ControllerState:
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
    pending_actions: list[dict[str, Any]] = field(default_factory=list)

    # Runtime config overrides (persisted across actions, togglable via /config).
    runtime_config: dict[str, Any] = field(default_factory=dict)

    # Log ring buffer for SSE streaming.
    _log_buffer: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=2000), repr=False
    )
    _log_seq: int = field(default=0, repr=False)
    _log_event: threading.Event = field(default_factory=threading.Event, repr=False)

    # Webhook URLs notified on action completion/error.
    webhook_urls: list[str] = field(default_factory=list)

    # Auto-heal: services that failed during bootstrap/reconcile.
    # Keyed by service_id → {error, failed_at, attempts, last_attempt}.
    failed_services: dict[str, dict[str, Any]] = field(default_factory=dict)

    def mark_service_failed(self, service_id: str, error: str) -> None:
        with self._lock:
            entry = self.failed_services.get(service_id, {
                "error": "", "failed_at": 0, "attempts": 0, "last_attempt": 0,
            })
            entry["error"] = str(error)[:1000]
            entry["attempts"] = entry.get("attempts", 0) + 1
            entry["last_attempt"] = time.time()
            if not entry.get("failed_at"):
                entry["failed_at"] = time.time()
            self.failed_services[service_id] = entry

    def mark_service_healed(self, service_id: str) -> None:
        with self._lock:
            self.failed_services.pop(service_id, None)

    def get_failed_services(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self.failed_services)

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
        # ``mark_initial_bootstrap_done`` re-acquires the lock to set
        # the flag AND persist it to the runtime-config sidecar, so a
        # controller restart on an already-bootstrapped install
        # restores the flag instead of resetting to ``False``.
        if not error:
            self.mark_initial_bootstrap_done()

    def mark_initial_bootstrap_done(self) -> None:
        """Flip ``initial_bootstrap_done`` to True AND persist it.

        Persistence rides the existing ``runtime-config.json`` file
        (same disk write as ``set_config``) — the flag stores under a
        leading-underscore key so ``runtime_config`` stays pure
        operator-tunable values. On startup, ``load_persisted_config``
        restores the flag, which means a controller restart on an
        already-bootstrapped install no longer wedges the dashboard
        banner on Queued waiting for a re-bootstrap that doesn't
        happen.
        """
        with self._lock:
            self.initial_bootstrap_done = True
            self._persist_runtime_config()

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
            clean_overrides = dict(overrides or {})
            triggered_by = str(clean_overrides.pop("_triggered_by", "system"))
            action = ActionRecord(
                id=f"{action_name}-{self._action_counter}",
                name=action_name,
                overrides=clean_overrides,
                timeout_seconds=timeout_seconds,
                triggered_by=triggered_by,
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

    # --- pending queue tracking ---

    def add_pending(self, action_name: str, priority: int, overrides: dict[str, Any] | None = None) -> None:
        """Track a newly queued action as pending."""
        with self._lock:
            clean = {k: v for k, v in (overrides or {}).items() if not k.startswith("_")}
            self.pending_actions.append({
                "name": action_name,
                "priority": priority,
                "queued_at": time.time(),
                "overrides": clean,
            })

    def pop_pending(self, action_name: str) -> None:
        """Remove the first pending entry matching this action name."""
        with self._lock:
            for i, item in enumerate(self.pending_actions):
                if item["name"] == action_name:
                    self.pending_actions.pop(i)
                    break

    def clear_pending(self) -> int:
        """Remove all pending actions. Returns count removed."""
        with self._lock:
            count = len(self.pending_actions)
            self.pending_actions.clear()
            return count

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

    # --- log streaming ---

    def append_log(self, line: str) -> None:
        """Append a log line to the ring buffer and notify SSE waiters."""
        with self._lock:
            self._log_seq += 1
            action = self.current_action.name if self.current_action else ""
            self._log_buffer.append((self._log_seq, time.time(), line, action))
            self._log_event.set()
            self._log_event.clear()

    def get_logs_since(self, after_seq: int = 0, action: str = "") -> list[tuple[int, float, str, str]]:
        """Return log entries with sequence > after_seq. Each entry: (seq, ts, msg, action).

        If *action* is non-empty, only entries whose action name matches are returned.
        """
        with self._lock:
            if action:
                return [
                    (seq, ts, msg, act)
                    for seq, ts, msg, act, *_ in self._log_buffer
                    if seq > after_seq and act == action
                ]
            return [(seq, ts, msg, act) for seq, ts, msg, act, *_ in self._log_buffer if seq > after_seq]

    @property
    def log_seq(self) -> int:
        return self._log_seq

    def wait_for_log(self, timeout: float = 30.0) -> bool:
        """Block until a new log line is appended (or timeout)."""
        return self._log_event.wait(timeout)

    # --- runtime config (persisted to disk so it survives restarts) ---

    _RUNTIME_CONFIG_FILE = "/srv-config/.controller/runtime-config.json"

    def set_config(self, key: str, value: Any) -> None:
        with self._lock:
            self.runtime_config[key] = value
            self._persist_runtime_config()

    def get_config(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self.runtime_config.get(key, default)

    def update_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Merge updates into runtime_config, return the full config."""
        with self._lock:
            self.runtime_config.update(updates)
            self._persist_runtime_config()
            return dict(self.runtime_config)

    def load_persisted_config(self) -> None:
        """Load runtime_config from disk (call at startup).

        Also restores webhook_urls, log level, and
        ``initial_bootstrap_done`` from persisted config — all stored
        under leading-underscore keys to distinguish them from
        operator-tunable runtime_config values.
        """
        import json
        path = Path(self._RUNTIME_CONFIG_FILE)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.runtime_config.update(data)
                # Restore webhooks
                saved_webhooks = data.get("_webhook_urls", [])
                if isinstance(saved_webhooks, list):
                    self.webhook_urls = list(saved_webhooks)
                # Restore log level
                saved_level = data.get("_log_level", "")
                if saved_level:
                    from media_stack.services.runtime_platform import set_log_level
                    set_log_level(saved_level)
                # Restore the first-run-done flag — ``initial_bootstrap
                # _done`` is in-memory by default, so a controller
                # restart would wedge the dashboard banner on Queued.
                # Once an install has ever bootstrapped successfully,
                # ``mark_initial_bootstrap_done`` writes
                # ``_initial_bootstrap_done: true`` here and we read it
                # back at startup.
                if data.get("_initial_bootstrap_done") is True:
                    self.initial_bootstrap_done = True
            except Exception as exc:
                log_swallowed(exc)

    def _persist_runtime_config(self) -> None:
        """Write runtime_config to disk (called on every update).

        Also writes the leading-underscore sidecar fields
        (``_webhook_urls``, ``_log_level``, ``_initial_bootstrap_done``)
        alongside ``runtime_config`` so a single file holds every
        piece of restart-resilient ControllerState.
        """
        import json
        path = Path(self._RUNTIME_CONFIG_FILE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = dict(self.runtime_config)
            if self.webhook_urls:
                payload["_webhook_urls"] = list(self.webhook_urls)
            if self.initial_bootstrap_done:
                payload["_initial_bootstrap_done"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            log_swallowed(exc)

    # --- serialization ---

    def to_dict(self) -> dict[str, Any]:
        """Serialize the public ``/status`` response.

        ADR-0005 Phase 5a retired the legacy bootstrap-progress
        fields (``phase``, ``phases_completed``, ``current_action``)
        — they duplicated the Job framework's running-tree + history
        view. Consumers that need live progress now read from
        ``/api/jobs/running`` + ``/api/jobs?history`` instead.

        The dataclass fields themselves stay for internal
        bookkeeping (``set_phase`` / ``begin_action`` / etc. still
        write them); they're just no longer part of the wire shape.
        """
        with self._lock:
            elapsed = None
            if self.started_at is not None:
                end = self.completed_at or time.time()
                elapsed = round(end - self.started_at, 1)
            return {
                "started_at": self.started_at,
                "completed_at": self.completed_at,
                "elapsed_seconds": elapsed,
                "error": self.error,
                "preflight_results": dict(self.preflight_results),
                "app_status": dict(self.app_status),
                "run_overrides": dict(self.run_overrides),
                "initial_bootstrap_done": self.initial_bootstrap_done,
                "runtime_config": dict(self.runtime_config),
                "webhook_urls": list(self.webhook_urls),
                "action_history": [a.to_dict() for a in self.action_history],
                "pending_actions": [dict(p) for p in self.pending_actions],
                "failed_services": dict(self.failed_services),
            }
