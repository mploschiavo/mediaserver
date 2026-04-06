"""Phase lifecycle tracking helpers for CLI orchestration scripts."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

LogFn = Callable[[str], None]


@dataclass
class PhaseTracker:
    """Track phase start/end timestamps and print a summary."""

    info: LogFn
    warn: LogFn
    run_start_epoch: int = field(default_factory=lambda: int(time.time()))
    current_phase: str = ""
    current_start: int = 0
    names: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    seconds: list[int] = field(default_factory=list)

    def start(self, phase_name: str) -> None:
        self.current_phase = phase_name
        self.current_start = int(time.time())
        self.info(f"[PHASE] START: {phase_name}")

    def end(self, result: str = "ok") -> None:
        now = int(time.time())
        if self.current_phase:
            elapsed = max(0, now - self.current_start)
            self.names.append(self.current_phase)
            self.results.append(result)
            self.seconds.append(elapsed)
            if result == "ok":
                self.info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                self.info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                self.warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_start = 0

    def summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        self.info(f"Phase Summary (total {total}s)")
        if not self.names:
            self.info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.names):
            self.info(f"  {name} => {self.results[idx]} ({self.seconds[idx]}s)")
