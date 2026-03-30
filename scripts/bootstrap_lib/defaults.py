"""Helpers for loading JSON defaults shipped with bootstrap scripts."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]


def load_json_default(
    defaults_dir: Path,
    filename: str,
    fallback: Any,
    *,
    log: LogFn | None = None,
) -> Any:
    path = defaults_dir / filename
    if not path.exists():
        if log is not None:
            log(f"[WARN] Bootstrap defaults file missing: {path}. Using in-code fallback.")
        return copy.deepcopy(fallback)

    try:
        raw = path.read_text(encoding="utf-8")
        loaded = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive parsing guard
        if log is not None:
            log(
                f"[WARN] Failed reading bootstrap defaults file {path} ({exc}). "
                "Using in-code fallback."
            )
        return copy.deepcopy(fallback)

    return loaded
