from __future__ import annotations

import os
from urllib import parse


def normalize_url(url: str) -> str:
    return str(url or "").rstrip("/")


def normalize_base_path(path_value: str | None) -> str:
    value = str(path_value or "").strip()
    if value in ("", "/"):
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value.rstrip("/")


def parse_service_url(url: str, default_port: int) -> dict[str, object]:
    parsed = parse.urlparse(normalize_url(url))
    if not parsed.scheme or not parsed.hostname:
        raise RuntimeError(f"Invalid service URL: {url}")

    return {
        "hostname": parsed.hostname,
        "port": int(parsed.port or default_port),
        "use_ssl": parsed.scheme.lower() == "https",
        "base_url": normalize_base_path(parsed.path),
    }


def to_int(value, fallback=None):
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def coerce_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def bool_cfg(cfg: dict, key: str, default=False) -> bool:
    if key not in cfg:
        return bool(default)
    return bool(cfg.get(key))


def env_truthy(name: str, default=False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return bool(default)
    return str(value).strip().lower() in ("1", "true", "yes", "on")
