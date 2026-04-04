"""Thread-safe bootstrap run state shared between API server and runner."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BootstrapState:
    """Mutable state shared between the HTTP API thread and the bootstrap runner thread."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    phase: str = "idle"
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    phases_completed: list[str] = field(default_factory=list)
    preflight_results: dict[str, dict[str, Any]] = field(default_factory=dict)

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

    def record_preflight(self, name: str, result: dict[str, Any]) -> None:
        with self._lock:
            self.preflight_results[name] = dict(result)

    @property
    def is_running(self) -> bool:
        return self.phase == "running"

    @property
    def is_complete(self) -> bool:
        return self.phase in ("complete", "error")

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
            }
