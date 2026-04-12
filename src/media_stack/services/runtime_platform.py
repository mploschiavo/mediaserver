#!/usr/bin/env python3
"""Generic runtime platform adapters shared by bootstrap entrypoints."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from media_stack.adapters.common import bool_cfg as _lib_bool_cfg
from media_stack.adapters.common import coerce_list as _lib_coerce_list
from media_stack.adapters.common import env_truthy as _lib_env_truthy
from media_stack.adapters.common import normalize_base_path as _lib_normalize_base_path
from media_stack.adapters.common import normalize_url as _lib_normalize_url
from media_stack.adapters.common import parse_service_url as _lib_parse_service_url
from media_stack.adapters.common import to_int as _lib_to_int
from media_stack.adapters.defaults import load_json_default as _lib_load_json_default
from media_stack.adapters.http_client import http_request as _lib_http_request

from media_stack.services.runtime_service_registry import (
    resolve_app_service_class,
    set_runtime_context_cfg,
)

BOOTSTRAP_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "contracts"

# ---------------------------------------------------------------------------
# Log-level-aware logger
# ---------------------------------------------------------------------------
# Supported prefixes: [DEBUG], [INFO], [OK], [WARN], [ERR], [ERROR], [TRACE]
# Messages without a recognized prefix default to INFO level.
# Set MEDIA_STACK_LOG_LEVEL=DEBUG to see all, INFO (default) to hide DEBUG.

_LOG_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_PREFIX_TO_LEVEL = {
    "DEBUG": 0, "INFO": 1, "OK": 1, "WAIT": 1, "RETRY": 1,
    "CRED": 1, "ACTION": 1, "JOB": 1, "HEAL": 1,
    "WARN": 2, "ERR": 3, "ERROR": 3, "TRACE": 0,
}
_current_log_level = _LOG_LEVEL_ORDER.get(
    os.environ.get("MEDIA_STACK_LOG_LEVEL", "INFO").upper(), 1
)


def set_log_level(level: str) -> str:
    """Change log level at runtime. Returns the new level name."""
    global _current_log_level
    level = level.upper()
    if level not in _LOG_LEVEL_ORDER:
        return get_log_level()
    _current_log_level = _LOG_LEVEL_ORDER[level]
    os.environ["MEDIA_STACK_LOG_LEVEL"] = level
    return level


def get_log_level() -> str:
    """Return the current log level name."""
    for name, val in _LOG_LEVEL_ORDER.items():
        if val == _current_log_level:
            return name
    return "INFO"


def _extract_level(msg: str) -> int:
    """Parse [PREFIX] from message and return numeric level. Default=INFO."""
    stripped = msg.lstrip()
    if stripped.startswith("["):
        bracket_end = stripped.find("]", 1)
        if bracket_end != -1:
            prefix = stripped[1:bracket_end].upper()
            return _PREFIX_TO_LEVEL.get(prefix, 1)
    return 1  # Default to INFO


def log(msg):
    if _extract_level(str(msg)) < _current_log_level:
        return
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    print(f"[{ts}] {msg}", flush=True)


def normalize_url(url):
    return _lib_normalize_url(url)


def http_request(base_url, path, api_key=None, method="GET", payload=None, timeout=20):
    return _lib_http_request(
        base_url,
        path,
        api_key=api_key,
        method=method,
        payload=payload,
        timeout=timeout,
    )


def wait_for_service(name, base_url, path, timeout_seconds):
    interval = int(os.environ.get("BOOTSTRAP_WAIT_INTERVAL_SECONDS", "3"))
    heartbeat = int(os.environ.get("BOOTSTRAP_WAIT_HEARTBEAT_SECONDS", "15"))
    interval = max(1, interval)
    heartbeat = max(interval, heartbeat)
    log(f"[DEBUG] wait_for_service: name={name}, url={base_url}{path}, "
        f"timeout={timeout_seconds}s, interval={interval}s, heartbeat={heartbeat}s")

    deadline = time.time() + timeout_seconds
    start = time.time()
    next_heartbeat = start
    attempt = 0
    last_status = None
    last_error = None

    while time.time() < deadline:
        attempt += 1
        try:
            status, _, _ = http_request(base_url, path, timeout=10)
            last_status = status
            last_error = None
            if 200 <= status < 500:
                log(f"[OK] {name} reachable at {base_url}{path} (HTTP {status})")
                return
        except Exception as exc:
            last_error = str(exc)

        now = time.time()
        if now >= next_heartbeat:
            elapsed = int(now - start)
            remaining = int(max(0, deadline - now))
            status_fragment = (
                f"last HTTP {last_status}" if last_status is not None else "no HTTP response yet"
            )
            err_fragment = f"; last error: {last_error}" if last_error else ""
            log(
                f"[WAIT] {name} not ready yet at {base_url}{path} "
                f"(attempt={attempt}, elapsed={elapsed}s, remaining={remaining}s, "
                f"{status_fragment}{err_fragment})"
            )
            next_heartbeat = now + heartbeat

        time.sleep(interval)

    elapsed = int(time.time() - start)
    raise RuntimeError(
        f"Timed out waiting for {name} at {base_url}{path} after {elapsed}s "
        f"(attempts={attempt}, last_status={last_status}, last_error={last_error})"
    )


def resolve_path(base_root, maybe_relative):
    p = Path(str(maybe_relative))
    if p.is_absolute():
        return p
    return Path(base_root) / p


def normalize_base_path(path_value):
    return _lib_normalize_base_path(path_value)


def parse_service_url(url, default_port):
    return _lib_parse_service_url(url, default_port)


def to_int(value, fallback=None):
    return _lib_to_int(value, fallback=fallback)


def coerce_list(value):
    return _lib_coerce_list(value)


def bool_cfg(cfg, key, default):
    return _lib_bool_cfg(cfg, key, default)


def env_truthy(name, default=False):
    return _lib_env_truthy(name, default=default)


def load_bootstrap_default_json(filename, fallback):
    return _lib_load_json_default(
        BOOTSTRAP_DEFAULTS_DIR,
        filename,
        fallback,
        log=log,
    )


def deep_merge_objects(base_obj, override_obj):
    if not isinstance(base_obj, dict):
        base_obj = {}
    if not isinstance(override_obj, dict):
        return json.loads(json.dumps(base_obj))

    out = json.loads(json.dumps(base_obj))
    for key, value in override_obj.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge_objects(out.get(key), value)
            continue
        out[key] = json.loads(json.dumps(value))
    return out


def field_map(field_list):
    out = {}
    for item in field_list or []:
        name = item.get("name")
        if not name:
            continue
        out[name] = item.get("value", "")
    return out


def field_list(mapping):
    return [{"name": key, "value": value} for key, value in mapping.items()]


def normalize_token(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def resolve_env_placeholder(value):
    if isinstance(value, str):
        raw = value.strip()
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw)
        if match:
            return os.environ.get(match.group(1), "")
    return value


def find_component_by_implementation(components, implementation):
    target = str(implementation or "").strip()
    for component in components or []:
        if str((component or {}).get("implementation") or "").strip() == target:
            return component
    return None


__all__ = [
    "BOOTSTRAP_DEFAULTS_DIR",
    "bool_cfg",
    "coerce_list",
    "env_truthy",
    "field_list",
    "field_map",
    "find_component_by_implementation",
    "deep_merge_objects",
    "get_log_level",
    "http_request",
    "load_bootstrap_default_json",
    "log",
    "normalize_base_path",
    "normalize_token",
    "normalize_url",
    "parse_service_url",
    "resolve_app_service_class",
    "resolve_env_placeholder",
    "resolve_path",
    "set_log_level",
    "set_runtime_context_cfg",
    "to_int",
    "wait_for_service",
]
