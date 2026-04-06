"""Shared utility helpers for typed bootstrap config models."""

from __future__ import annotations

from typing import Any, Callable


def to_int(value: Any, fallback: int | None = None) -> int | None:
    try:
        if value is None:
            return fallback
        return int(value)
    except (TypeError, ValueError):
        return fallback


def coerce_bool_opt(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def coerce_str_list_opt(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    token = str(value).strip()
    return [token] if token else []


def normalize_by_app_key(
    key: str,
    canonicalize: Callable[[str], str] | None = None,
) -> str:
    raw = str(key or "").strip()
    if not raw:
        return ""
    if canonicalize:
        candidate = str(canonicalize(raw)).strip()
        if candidate:
            return candidate.lower()
    return raw.lower()
