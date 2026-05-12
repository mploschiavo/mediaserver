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

from media_stack.core.exceptions import MediaStackError
from media_stack.core.platforms.kubernetes.kube_client import resolve_kubectl_binary
from media_stack.core.time_utils import ISO_8601_TZ_OFFSET, ISO_8601_UTC_Z  # noqa: F401


# ---------------------------------------------------------------------------
# CLI helpers (class-based per ADR-0012)
# ---------------------------------------------------------------------------


class CliCommonHelpers:
    """Class-based wrapper for shared CLI helpers (ADR-0012)."""

    def ts(self) -> str:
        """ISO timestamp for log lines."""
        return time.strftime(ISO_8601_TZ_OFFSET)

    def info(self, message: str) -> None:
        print(f"[{sys.modules[__name__].ts()}] [INFO] {message}", flush=True)

    def warn(self, message: str) -> None:
        print(
            f"[{sys.modules[__name__].ts()}] [WARN] {message}",
            file=sys.stderr,
            flush=True,
        )

    def err(self, message: str) -> None:
        print(
            f"[{sys.modules[__name__].ts()}] [ERR] {message}",
            file=sys.stderr,
            flush=True,
        )

    def run_command(
        self,
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

    def kube_cmd(self) -> list[str]:
        return resolve_kubectl_binary()

    def repo_root_from_script_file(self, script_file: str) -> Path:
        resolved = Path(script_file).resolve()
        for candidate in (resolved.parent, *resolved.parents):
            if (
                (candidate / "pyproject.toml").exists()
                and (candidate / "src" / "media_stack").is_dir()
                and (candidate / "contracts").is_dir()
            ):
                return candidate
        return resolved.parent


_INSTANCE = CliCommonHelpers()

# Module-level aliases preserve every public name with the same signature so
# `from media_stack.core.cli_common import ts, info, warn, err, ...` keeps
# working for every CLI that imports them.
ts = _INSTANCE.ts
info = _INSTANCE.info
warn = _INSTANCE.warn
err = _INSTANCE.err
run_command = _INSTANCE.run_command
kube_cmd = _INSTANCE.kube_cmd
repo_root_from_script_file = _INSTANCE.repo_root_from_script_file


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
        sys.modules[__name__].info(f"[PHASE] START: {phase_name}")

    def end(self, result: str) -> None:
        now = int(time.time())
        if self.current_phase:
            elapsed = now - self.current_start
            self.names.append(self.current_phase)
            self.results.append(result)
            self.seconds.append(elapsed)
            module = sys.modules[__name__]
            if result == "ok":
                module.info(f"[PHASE] DONE: {self.current_phase} ({elapsed}s)")
            elif result == "skipped":
                module.info(f"[PHASE] SKIP: {self.current_phase} ({elapsed}s)")
            else:
                module.warn(f"[PHASE] FAIL: {self.current_phase} ({elapsed}s)")
        self.current_phase = ""
        self.current_start = 0

    def summary(self) -> None:
        total = int(time.time()) - self.run_start_epoch
        module = sys.modules[__name__]
        module.info(f"Phase Summary (total {total}s)")
        if not self.names:
            module.info("  (no phases recorded)")
            return
        for idx, name in enumerate(self.names):
            module.info(f"  {name} => {self.results[idx]} ({self.seconds[idx]}s)")

    # Alias used by some callers.
    print_summary = summary
