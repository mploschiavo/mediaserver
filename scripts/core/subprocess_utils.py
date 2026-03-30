"""Subprocess execution utilities."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable, Mapping

from .exceptions import CommandExecutionError


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Thin adapter for subprocess with consistent error handling."""

    def run(
        self,
        args: Iterable[str],
        *,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        proc = subprocess.run(
            list(args),
            check=False,
            capture_output=True,
            text=True,
            env=dict(env) if env is not None else None,
            timeout=timeout,
        )
        result = CommandResult(
            args=list(args),
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
        if check and result.returncode != 0:
            raise CommandExecutionError(
                f"Command failed: {' '.join(result.args)}",
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result
