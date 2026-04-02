"""Shared discovery-list helper functions."""

from __future__ import annotations

import re
from typing import Any


def coerce_for_example(value: Any, example: Any) -> Any:
    if isinstance(example, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(example, int) and not isinstance(example, bool):
        try:
            if value is None:
                return value
            return int(value)
        except Exception:
            return value
    return value


def normalize_title(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text)


def service_to_int(value: Any, default: int | None = None) -> int | None:
    try:
        if value is None:
            return default
        return int(value)
    except Exception:
        return default


def pick_series_lookup_candidate(
    lookup_payload: list[Any], target_name: str
) -> dict[str, Any] | None:
    candidates = [item for item in lookup_payload if isinstance(item, dict)]
    if not candidates:
        return None
    target_token = normalize_title(target_name)
    for item in candidates:
        if normalize_title(item.get("title")) == target_token:
            return item
    for item in candidates:
        if service_to_int(item.get("tvdbId")):
            return item
    return candidates[0]
