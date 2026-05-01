"""Default command runner shared by workflow services."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from media_stack.core.cli_common import run_command


class WorkflowCommandRunnerService:
    """Runs external commands through the project CLI helper."""

    def run_text(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> str:
        proc = run_command(command, check=check, env=env)
        return (proc.stdout or "").strip()

    def run_json(
        self,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        check: bool = True,
    ) -> Any:
        output = self.run_text(command, env=env, check=check)
        return json.loads(output) if output else None
