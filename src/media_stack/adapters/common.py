from __future__ import annotations

import os
from urllib import parse


class CommonAdapters:
    """Common adapter utility functions for URL normalization and config helpers."""

    @staticmethod
    def normalize_url(url: str) -> str:
        return str(url or "").rstrip("/")

    @staticmethod
    def normalize_base_path(path_value: str | None) -> str:
        value = str(path_value or "").strip()
        if value in ("", "/"):
            return ""
        if not value.startswith("/"):
            value = f"/{value}"
        return value.rstrip("/")

    @staticmethod
    def parse_service_url(url: str, default_port: int) -> dict[str, object]:
        parsed = parse.urlparse(CommonAdapters.normalize_url(url))
        if not parsed.scheme or not parsed.hostname:
            raise RuntimeError(f"Invalid service URL: {url}")

        return {
            "hostname": parsed.hostname,
            "port": int(parsed.port or default_port),
            "use_ssl": parsed.scheme.lower() == "https",
            "base_url": CommonAdapters.normalize_base_path(parsed.path),
        }

    @staticmethod
    def to_int(value, fallback=None):
        if value is None:
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def coerce_list(value):
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        return [value]

    @staticmethod
    def bool_cfg(cfg: dict, key: str, default=False) -> bool:
        if key not in cfg:
            return bool(default)
        return bool(cfg.get(key))

    @staticmethod
    def env_truthy(name: str, default=False) -> bool:
        value = os.environ.get(name)
        if value is None:
            return bool(default)
        return str(value).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = CommonAdapters()
normalize_url = _instance.normalize_url
normalize_base_path = _instance.normalize_base_path
parse_service_url = _instance.parse_service_url
to_int = _instance.to_int
coerce_list = _instance.coerce_list
bool_cfg = _instance.bool_cfg
env_truthy = _instance.env_truthy
