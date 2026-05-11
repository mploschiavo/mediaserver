"""JsonPathFormatter — render an iterable of JSON-path parts as ``$.foo[0]``.

ADR-0015 Phase 7b. Pre-Phase-7b this lived as the
:func:`ValidateControllerConfigCommand.format_path` method in
``cli/commands/validate_controller_config_main.py``. Splitting onto
its own class isolates the "human-readable JSON pointer" utility
from the validation logic that consumes it.

Pattern: Value Object / pure formatter. No state, no IO. The
class wrapper exists to keep the module's top-level
``FunctionDef`` count at zero (ADR-0012).
"""

from __future__ import annotations

from typing import Iterable


class JsonPathFormatter:
    """Render a sequence of path parts as ``$.foo[0].bar``."""

    def format(self, path_parts: Iterable[str | int]) -> str:
        parts = list(path_parts)
        if not parts:
            return "$"
        out = "$"
        for part in parts:
            if isinstance(part, int):
                out += f"[{part}]"
            else:
                out += f".{part}"
        return out


__all__ = ["JsonPathFormatter"]
