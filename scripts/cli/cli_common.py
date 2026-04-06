#!/usr/bin/env python3
"""Shared helpers for migrated script CLIs."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from core.exceptions import MediaStackError
from core.platforms.kubernetes.kube_client import resolve_kubectl_binary


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def ts() -> str:
    """ISO timestamp for log lines."""
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def info(message: str) -> None:
    print(f"[{ts()}] [INFO] {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{ts()}] [WARN] {message}", file=sys.stderr, flush=True)


def err(message: str) -> None:
    print(f"[{ts()}] [ERR] {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Phase tracking
# ---------------------------------------------------------------------------

@dataclass
class PhaseTracker:
    """Track named phases with timing and pass/fail results."""

    run_start_epoch: int = field(default_factory=lambda: int(time.time()))
    current_phase: str = ""
    current_start: int = 0
    names: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)
    seconds: list[int] = field(default_factory=list)

    def start(self, phase_name: str) -> None:
        self.current_phase = phase_name
        self.current_start = int(time.time())
        info(f"[PHASE] START: {phase_name}")

    def end(self, result: str) -> None:
        now = int(time.time())
        if self.current_phase:
            elapsed = now - self.current_start
            self.names.append(self.current_phase)
            self.results.append(result)
            self.seconds.append(elapsed)
            if result == "ok":
                info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_start = 0

    def summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        info(f"Phase Summary (total {total}s)")
        if not self.names:
            info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.names):
            info(f"  {name} => {self.results[idx]} ({self.seconds[idx]}s)")

    # Alias used by some callers.
    print_summary = summary


def run_command(
    cmd: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(cmd),
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        env=(dict(os.environ) | dict(env)) if env is not None else None,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        raise MediaStackError(f"Command failed ({' '.join(cmd)}): {detail}")
    return proc


def kube_cmd() -> list[str]:
    return resolve_kubectl_binary()


def repo_root_from_script_file(script_file: str) -> Path:
    resolved = Path(script_file).resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / "bootstrap" / "media-stack.bootstrap.json").exists() and (
            candidate / "scripts"
        ).exists():
            return candidate
    # Backward-compatible fallback for expected scripts/cli/<name>.py paths.
    if len(resolved.parents) >= 3:
        return resolved.parents[2]
    return resolved.parent
