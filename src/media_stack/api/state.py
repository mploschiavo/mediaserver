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


# ``ActionStatus`` + ``ActionRecord`` are retained as a small public
# value-object type so external operator scripts that read action
# records (e.g. via the legacy ``GET /jobs.history[].jobs``
# tabulation) still get a typed shape. Production no longer
# constructs them — Phase 5c.4c retired the ``ControllerState``
# action-lifecycle surface that did. The class can move to a
# domain types module in a follow-up; leaving it here keeps the
# import paths external scripts depend on stable.
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


# ADR-0010 Phase 7 cleanup #7 — canonical name of the historical
# bootstrap-completion flag. Bound to a constant so the dict-backed
# deployment-flags machinery + the back-compat ``initial_bootstrap_done``
# attribute share one source of truth instead of repeating the string
# (which would tick the duplicate-strings ratchet).
_INITIAL_BOOTSTRAP_DONE_FLAG = "initial_bootstrap_done"


@dataclass
class ControllerState:
    """Mutable state shared between the HTTP API thread and the bootstrap runner thread.

    ADR-0005 Phase 5c.4c retired the action-lifecycle surface
    (``current_action`` / ``action_history`` fields, plus
    ``start_action`` / ``finish_action`` / ``cancel_action`` /
    ``add_pending`` / ``pop_pending`` / ``get_action`` /
    ``action_running``). Authoritative in-flight + completed run
    state now lives in the Job framework (``run_history`` JSONL on
    disk + ``GET /api/jobs/running`` + ``GET /api/jobs?history``).
    """

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Legacy fields — kept for backward compatibility with host-side polling.
    phase: str = "idle"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    phases_completed: list[str] = field(default_factory=list)
    preflight_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    app_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    run_overrides: dict[str, Any] = field(default_factory=dict)

    # Deployment-state flag — has this install ever bootstrapped
    # successfully? Persisted to the runtime-config sidecar so a
    # controller restart on an already-bootstrapped install
    # doesn't wedge the dashboard banner.
    #
    # ADR-0010 Phase 7 cleanup #7: backed by ``_deployment_flags``
    # so plugins can declare their own setup-complete flags via the
    # ``marks_setup_complete: <flag-name>`` contract field. The
    # legacy attribute stays for back-compat with callers that read
    # it directly; ``set_deployment_flag`` and
    # ``is_deployment_flag_set`` are the canonical surface.
    initial_bootstrap_done: bool = False
    _deployment_flags: dict[str, bool] = field(default_factory=dict)
    # ``pending_actions`` is now always empty — the in-process
    # priority queue is the source of truth for queued work. The
    # field stays in ``to_dict()`` for back-compat with the public
    # ``/status`` shape (consumers tolerate an empty list); a
    # follow-up phase can remove it once every external operator
    # script has rolled past it.
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
        """Backwards-compatible shim — delegates to
        ``set_deployment_flag(_INITIAL_BOOTSTRAP_DONE_FLAG)``. Kept
        so callers that imported the named method continue to work
        without a churn-the-world rename."""
        self.set_deployment_flag(_INITIAL_BOOTSTRAP_DONE_FLAG)

    def set_deployment_flag(self, name: str) -> None:
        """Set a named deployment-state flag and persist.

        ADR-0010 Phase 7 cleanup #7. The contract field
        ``marks_setup_complete: <flag-name>`` on a Job entry causes
        ``JobLifecycleMetadataHandler`` to call this with the
        declared name on the Job's successful completion. The flag
        is stored in ``_deployment_flags`` and persisted to the
        runtime-config sidecar; a controller restart restores it.

        The historical ``initial_bootstrap_done`` attribute is
        synced for back-compat with callers (and the persisted
        format) that read the named field directly.
        """
        if not name:
            return
        with self._lock:
            self._deployment_flags[name] = True
            if name == _INITIAL_BOOTSTRAP_DONE_FLAG:
                self.initial_bootstrap_done = True
            self._persist_runtime_config()

    def is_deployment_flag_set(self, name: str) -> bool:
        """Return whether ``name`` is set. The
        ``initial_bootstrap_done`` flag also reads the legacy
        attribute for back-compat with persisted state from before
        the dict-backed mechanism was introduced."""
        with self._lock:
            if self._deployment_flags.get(name, False):
                return True
            if name == _INITIAL_BOOTSTRAP_DONE_FLAG:
                return bool(self.initial_bootstrap_done)
            return False

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

    # ADR-0005 Phase 5c.4c: action lifecycle methods retired.
    # ``start_action`` / ``finish_action`` / ``cancel_action`` /
    # ``add_pending`` / ``pop_pending`` / ``get_action`` /
    # ``action_running`` were duplicating the Job framework's
    # run_history surface. Replacement plumbing:
    #   * In-flight roots: ``run_history.get_running_tree()``
    #   * Completed runs: ``framework.get_job_history()``
    #   * Cancellation: ``framework.request_cancel()`` + the per-
    #     action watchdog in ``cli/commands/controller_serve.py``
    #   * Log-line action tag: ``runtime_platform.current_action_tag``

    def clear_pending(self) -> int:
        """Remove all pending actions. Returns count removed.

        Vestigial after Phase 5c.4c (``add_pending`` retired so the
        list never grows). Kept on the public surface because
        operator clear-queue tooling still calls it; can be removed
        once the public ``/status``'s ``pending_actions`` envelope
        retires too.
        """
        with self._lock:
            count = len(self.pending_actions)
            self.pending_actions.clear()
            return count

    # --- log streaming ---

    def append_log(self, line: str) -> None:
        """Append a log line to the ring buffer and notify SSE waiters.

        ADR-0005 Phase 5c.4c: the per-line action tag is read off the
        ``runtime_platform`` contextvar (set by the action loop's
        ``current_action_tag(name)`` ``with`` block), not the retired
        ``current_action`` field on this dataclass. The SSE shape and
        filter semantics (``get_logs_since(action=...)``) are
        unchanged — we just stopped reading from a dataclass field
        that no longer exists.
        """
        from media_stack.services.runtime_platform import (
            get_current_action_tag,
        )
        with self._lock:
            self._log_seq += 1
            action = get_current_action_tag()
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
                    self._deployment_flags[
                        _INITIAL_BOOTSTRAP_DONE_FLAG
                    ] = True
                # ADR-0010 Phase 7 cleanup #7 — restore plugin-
                # declared deployment flags. Lifted into a helper to
                # keep this method shallow (under the deeply-nested
                # ratchet).
                self._restore_persisted_deployment_flags(
                    data.get("_deployment_flags"),
                )
            except Exception as exc:
                log_swallowed(exc)

    def _restore_persisted_deployment_flags(
        self, raw: Any,
    ) -> None:
        """Hydrate ``_deployment_flags`` from the persisted dict.
        Older persisted format only had ``_initial_bootstrap_done``;
        this picks up any additional flags set via
        ``set_deployment_flag(<name>)`` after the dict-backed
        mechanism landed."""
        if not isinstance(raw, dict):
            return
        for fname, fvalue in raw.items():
            if not isinstance(fname, str) or not bool(fvalue):
                continue
            self._deployment_flags[fname] = True
            if fname == _INITIAL_BOOTSTRAP_DONE_FLAG:
                self.initial_bootstrap_done = True

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
            if self._deployment_flags:
                # Round-trip the dict-backed flags so plugin-declared
                # flags survive a restart alongside the legacy
                # ``_initial_bootstrap_done`` boolean.
                payload["_deployment_flags"] = dict(self._deployment_flags)
            path.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as exc:
            log_swallowed(exc)

    # --- serialization ---

    def to_dict(self) -> dict[str, Any]:
        """Serialize the public ``/status`` response.

        ADR-0005 Phase 5a retired ``phase`` / ``phases_completed`` /
        ``current_action`` from the wire; Phase 5c.4c retires
        ``action_history`` for the same reason — it duplicated the
        Job framework's run-history view. Consumers that need
        live progress read from ``/api/jobs/running`` +
        ``/api/jobs?history`` instead.
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
                "pending_actions": [dict(p) for p in self.pending_actions],
                "failed_services": dict(self.failed_services),
            }
