"""Helpers for loading JSON defaults shipped with bootstrap scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]


def load_json_default(
    defaults_dir: Path,
    filename: str,
    default_value: Any,
    *,
    log: LogFn | None = None,
) -> Any:
    path = defaults_dir / filename
    if not path.exists():
        if default_value is not None:
            return default_value
        raise FileNotFoundError(f"Bootstrap defaults file missing: {path}")

    try:
        raw = path.read_text(encoding="utf-8")
        loaded = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive parsing guard
        raise ValueError(f"Failed reading bootstrap defaults file {path}: {exc}") from exc

    return loaded
