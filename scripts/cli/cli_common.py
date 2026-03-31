#!/usr/bin/env python3
"""Shared helpers for migrated script CLIs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Sequence

from core.exceptions import MediaStackError
from core.kube import resolve_kubectl_binary


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
    return Path(script_file).resolve().parents[2]
