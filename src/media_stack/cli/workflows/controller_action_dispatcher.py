"""ControllerActionDispatcher — drain the action queue, run jobs, fire webhooks.

ADR-0015 Phase 7e. Pre-Phase-7e the entire action-dispatch loop
(~150 LoC of ``_run_one_action`` + ``_action_loop`` closures with
a watchdog daemon thread) lived inline inside the 683-LoC
``_run_serve`` god method.

Phase 7e collapses it into three SRP classes:

* :class:`ActionWatchdog` — daemon thread that monitors a single
  action's timeout + emits heartbeats; trips
  :func:`request_cancel` on the framework's module-global flag
  when the budget is exceeded.
* :class:`SingleActionRunner` — runs one action through
  :func:`_dispatch_action`, returns ``(error_msg, elapsed_seconds)``.
* :class:`ControllerActionDispatcher` — owns the queue-drain loop,
  retry policy, success/failure webhook fan-out, and the
  shutdown path on :class:`KeyboardInterrupt`.

ADR-0005 Phase 5c.4c cancel semantics are preserved verbatim:
the framework's module-global ``_is_cancel_requested()`` flag is
the single observation point; cooperative cancel propagates
through ``JobContext.check_cancelled()``.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from media_stack.services.jobs import framework as _fw
from media_stack.services.runtime_platform import current_action_tag


_WATCHDOG_HEARTBEAT_SECONDS = 60
_WATCHDOG_POLL_INTERVAL_SECONDS = 1.0
_WATCHDOG_JOIN_TIMEOUT_SECONDS = 2.0
_RETRY_BACKOFF_CAP_SECONDS = 10.0


class ActionWatchdog:
    """Daemon thread: enforce per-action timeout via cooperative-cancel flag."""

    def __init__(
        self,
        action_name: str,
        instance_id: str,
        timeout_seconds: int,
        log: Callable[[str], None],
    ) -> None:
        self._action_name = action_name
        self._instance_id = instance_id
        self._timeout_seconds = timeout_seconds
        self._log = log
        self._cancel_event = threading.Event()
        self._timed_out = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def timed_out(self) -> bool:
        return self._timed_out.is_set()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._watch,
            daemon=True,
            name=f"action-watchdog-{self._instance_id}",
        )
        self._thread.start()

    def stop(self) -> None:
        self._cancel_event.set()
        if self._thread is not None:
            self._thread.join(timeout=_WATCHDOG_JOIN_TIMEOUT_SECONDS)

    def _watch(self) -> None:
        t0 = time.monotonic()
        next_heartbeat = t0 + _WATCHDOG_HEARTBEAT_SECONDS
        while not self._cancel_event.wait(timeout=_WATCHDOG_POLL_INTERVAL_SECONDS):
            now = time.monotonic()
            elapsed = now - t0
            if elapsed >= self._timeout_seconds:
                self._timed_out.set()
                self._log(
                    f"[ACTION] {self._action_name}: TIMED OUT after "
                    f"{elapsed:.0f}s (limit {self._timeout_seconds}s) — "
                    "requesting cooperative cancel"
                )
                _fw.request_cancel()
                return
            if now >= next_heartbeat:
                next_heartbeat = now + _WATCHDOG_HEARTBEAT_SECONDS
                self._log(
                    f"[ACTION] {self._action_name}: still running "
                    f"({elapsed:.0f}s elapsed, timeout {self._timeout_seconds}s)"
                )


class SingleActionRunner:
    """Run one action synchronously; return ``(error_msg, elapsed_seconds)``."""

    def __init__(
        self,
        args: object,
        state: object,
        default_timeout_seconds: int,
        log: Callable[[str], None],
        dispatch_action: Callable[[str, dict, object, object], None],
    ) -> None:
        self._args = args
        self._state = state
        self._default_timeout_seconds = default_timeout_seconds
        self._log = log
        # ``dispatch_action`` is constructor-injected so the workflow
        # layer doesn't reach across the ADR-0015 boundary into
        # ``cli/commands/controller_dispatch.py``. The caller passes
        # the function pointer; we just invoke it.
        self._dispatch_action = dispatch_action

    def run(
        self,
        action_name: str,
        overrides: dict,
        instance_id: str,
    ) -> tuple[str | None, float]:
        timeout_seconds = max(
            1, int(overrides.get("timeout") or self._default_timeout_seconds),
        )
        started = time.monotonic()
        watchdog = ActionWatchdog(action_name, instance_id, timeout_seconds, self._log)
        watchdog.start()

        # Reset the framework's module-global cancel flag for this run;
        # it persists across calls otherwise.
        _fw.clear_cancel()

        error_msg: str | None = None
        try:
            with current_action_tag(action_name):
                self._dispatch_action(action_name, overrides, self._args, self._state)
        except Exception as exc:  # noqa: BLE001 — dispatch raises any exception type
            error_msg = str(exc)
        finally:
            watchdog.stop()

        elapsed = round(time.monotonic() - started, 1)
        if watchdog.timed_out and not error_msg:
            error_msg = f"timed out after {timeout_seconds}s"
        if _fw._is_cancel_requested() and not error_msg:
            error_msg = "cancelled by user"
        _fw.clear_cancel()
        return error_msg, elapsed


class ControllerActionDispatcher:
    """Drain the action queue, run jobs, fire webhooks, retry on failure."""

    def __init__(
        self,
        action_queue: "queue.PriorityQueue",
        args: object,
        state: object,
        server: object,
        action_timeout_seconds: int,
        max_retries: int,
        log: Callable[[str], None],
        fire_webhooks: Callable[[object, str, dict], None],
        dispatch_action: Callable[[str, dict, object, object], None],
    ) -> None:
        self._queue = action_queue
        self._state = state
        self._server = server
        self._max_retries = max_retries
        self._log = log
        self._fire_webhooks = fire_webhooks
        self._runner = SingleActionRunner(
            args, state, action_timeout_seconds, log, dispatch_action,
        )
        self._instance_counter = 0
        self._instance_counter_lock = threading.Lock()
        self._action_timeout_seconds = action_timeout_seconds

    def next_instance_id(self, action_name: str) -> str:
        with self._instance_counter_lock:
            self._instance_counter += 1
            return f"{action_name}-{self._instance_counter}"

    def drain_forever(self) -> None:
        """Drain the action queue forever. Designed to run on a daemon thread."""
        while True:
            try:
                _prio, _seq, action_name, overrides = self._queue.get()
            except KeyboardInterrupt:
                self._log("[INFO] Shutting down bootstrap service")
                self._server.shutdown()
                return
            # Merge runtime_config into overrides so toggles like
            # auto_download_content propagate to ``_apply_overrides``.
            for cfg_key, cfg_val in self._state.runtime_config.items():
                overrides.setdefault(cfg_key, cfg_val)
            self._run_with_retries(action_name, overrides)

    def _run_with_retries(self, action_name: str, overrides: dict) -> None:
        retry_limit = int(overrides.pop("retry", self._max_retries))
        attempt = 0
        while True:
            attempt += 1
            instance_id = self.next_instance_id(action_name)
            suffix = (
                f" (attempt {attempt}/{retry_limit + 1})" if retry_limit > 0 else ""
            )
            self._log(
                f"[ACTION] {action_name} [{instance_id}]: dispatching "
                f"(timeout={self._action_timeout_seconds}s){suffix}"
            )
            error_msg, elapsed = self._runner.run(
                action_name, dict(overrides), instance_id,
            )
            if error_msg == "cancelled by user":
                self._log(f"[ACTION] {action_name}: cancelled")
                return
            if error_msg:
                self._log(f"[ERR] Action {action_name} failed: {error_msg}")
                if attempt <= retry_limit:
                    delay = min(_RETRY_BACKOFF_CAP_SECONDS, 2.0 ** (attempt - 1))
                    self._log(
                        f"[RETRY] {action_name}: retrying in {delay:.0f}s "
                        f"(attempt {attempt}/{retry_limit + 1})"
                    )
                    time.sleep(delay)
                    continue
                self._fire_webhooks(self._state, "action_error", {
                    "action": action_name,
                    "status": "error",
                    "error": error_msg,
                    "elapsed_seconds": elapsed,
                })
                return  # Exhausted retries.
            # Success — webhooks only; bootstrap-completion side effects
            # (mark-initial-bootstrap-done) flow through the contract-
            # declared trigger.
            self._fire_webhooks(self._state, "action_complete", {
                "action": action_name,
                "status": "complete",
                "elapsed_seconds": elapsed,
            })
            return


__all__ = [
    "ActionWatchdog",
    "ControllerActionDispatcher",
    "SingleActionRunner",
]
