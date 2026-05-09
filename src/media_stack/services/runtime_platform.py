#!/usr/bin/env python3
"""Generic runtime platform adapters shared by bootstrap entrypoints."""

from __future__ import annotations
from media_stack.core.time_utils import ISO_8601_TZ_OFFSET, ISO_8601_UTC_Z

import contextlib
import contextvars
import json
import os
import re
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

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
_LOG_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARN": 2, "ERROR": 3}
_PREFIX_TO_LEVEL = {
    "DEBUG": 0, "INFO": 1, "OK": 1, "WAIT": 1, "RETRY": 1,
    "CRED": 1, "ACTION": 1, "JOB": 1, "HEAL": 1,
    "WARN": 2, "ERR": 3, "ERROR": 3, "TRACE": 0,
}
_DEFAULT_LOG_LEVEL_NAME = "INFO"
_INFO_LEVEL_VALUE = _LOG_LEVEL_ORDER[_DEFAULT_LOG_LEVEL_NAME]
_BOOTSTRAP_WAIT_INTERVAL_ENV = "BOOTSTRAP_WAIT_INTERVAL_SECONDS"
_BOOTSTRAP_WAIT_HEARTBEAT_ENV = "BOOTSTRAP_WAIT_HEARTBEAT_SECONDS"
_BOOTSTRAP_WAIT_INTERVAL_DEFAULT = "3"
_BOOTSTRAP_WAIT_HEARTBEAT_DEFAULT = "15"
_LOG_LEVEL_ENV = "MEDIA_STACK_LOG_LEVEL"
_HTTP_PROBE_TIMEOUT_SECONDS = 10
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NORMALIZE_TOKEN_RE = re.compile(r"[^a-z0-9]+")

_current_log_level = _LOG_LEVEL_ORDER.get(
    os.environ.get(_LOG_LEVEL_ENV, _DEFAULT_LOG_LEVEL_NAME).upper(), _INFO_LEVEL_VALUE
)




# ---------------------------------------------------------------------------
# Current-action tag — replaces ControllerState.current_action.name for
# log-line tagging in the SSE ring buffer (ADR-0005 Phase 5c.4c).
#
# The action loop (controller_serve.py) wraps each in-flight dispatch in
# ``current_action_tag(name)`` so any ``runtime_platform.log`` call made
# from inside that dispatch — including those that flow through the
# ``_instrumented_log`` shim into ``state.append_log`` — observes the
# active action name. ``contextvars.ContextVar`` makes the tag
# thread-safe and copy-on-fork for daemon threads spawned mid-dispatch
# (the JobRunner's non_blocking branch); the previous implementation
# read ``state.current_action.name`` under a global mutex, which was
# functionally equivalent but coupled the SSE filter to the dataclass
# we're now retiring.
# ---------------------------------------------------------------------------

_current_action_tag: contextvars.ContextVar[str] = contextvars.ContextVar(
    "media_stack_current_action_tag", default="",
)


@contextlib.contextmanager
def current_action_tag(name: str) -> Iterator[None]:
    """Bind ``name`` as the active action tag for the duration of the
    ``with`` block. Restores the previous tag on exit (so nested
    bindings — e.g. an action that spawns a sub-dispatch — unwind
    cleanly).

    Stays as a module-level function: ``contextlib.contextmanager`` is
    the canonical Python shape for this contextvar pattern (per the
    pinned ratchet exemption in ADR-0005 Phase 5c.4c).
    """
    token = _current_action_tag.set(str(name or ""))
    try:
        yield
    finally:
        _current_action_tag.reset(token)


class RuntimePlatformService:
    """Wraps all runtime platform adapter functions."""

    def get_current_action_tag(self) -> str:
        """Return the active action name for the current execution context.

        Empty string means no action is bound (SSE consumers treat the
        empty tag as "untagged" the same way they did when
        ``state.current_action`` was None).
        """
        return _current_action_tag.get()

    def set_log_level(self, level: str) -> str:
        global _current_log_level
        level = level.upper()
        if level not in _LOG_LEVEL_ORDER:
            return self.get_log_level()
        _current_log_level = _LOG_LEVEL_ORDER[level]
        os.environ[_LOG_LEVEL_ENV] = level
        return level

    def get_log_level(self) -> str:
        for name, val in _LOG_LEVEL_ORDER.items():
            if val == _current_log_level:
                return name
        return _DEFAULT_LOG_LEVEL_NAME

    def log(self, msg: Any) -> None:
        # Dispatch through the module alias so ``mock.patch`` of
        # ``runtime_platform._extract_level`` keeps intercepting (the
        # subprocess-log filter pattern + tests rely on this).
        if sys.modules[__name__]._extract_level(str(msg)) < _current_log_level:
            return
        ts = time.strftime(ISO_8601_TZ_OFFSET)
        print(f"[{ts}] {msg}", flush=True)

    def normalize_url(self, url: Any) -> str:
        return _lib_normalize_url(url)

    def http_request(
        self,
        base_url: str,
        path: str,
        api_key: str | None = None,
        method: str = "GET",
        payload: Any = None,
        timeout: int = 20,
    ) -> tuple[int, dict, bytes]:
        return _lib_http_request(
            base_url, path,
            api_key=api_key, method=method, payload=payload, timeout=timeout,
        )

    def wait_for_service(
        self,
        name: str,
        base_url: str,
        path: str,
        timeout_seconds: int,
    ) -> None:
        interval = int(os.environ.get(
            _BOOTSTRAP_WAIT_INTERVAL_ENV, _BOOTSTRAP_WAIT_INTERVAL_DEFAULT,
        ))
        heartbeat = int(os.environ.get(
            _BOOTSTRAP_WAIT_HEARTBEAT_ENV, _BOOTSTRAP_WAIT_HEARTBEAT_DEFAULT,
        ))
        interval = max(1, interval)
        heartbeat = max(interval, heartbeat)
        self.log(f"[DEBUG] wait_for_service: name={name}, url={base_url}{path}, "
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
                status, _, _ = self.http_request(
                    base_url, path, timeout=_HTTP_PROBE_TIMEOUT_SECONDS,
                )
                last_status = status
                last_error = None
                if 200 <= status < 500:
                    self.log(f"[OK] {name} reachable at {base_url}{path} (HTTP {status})")
                    return
            except Exception as exc:
                last_error = str(exc)
            now = time.time()
            if now >= next_heartbeat:
                elapsed = int(now - start)
                remaining = int(max(0, deadline - now))
                status_fragment = f"last HTTP {last_status}" if last_status is not None else "no HTTP response yet"
                err_fragment = f"; last error: {last_error}" if last_error else ""
                self.log(f"[WAIT] {name} not ready yet at {base_url}{path} "
                    f"(attempt={attempt}, elapsed={elapsed}s, remaining={remaining}s, "
                    f"{status_fragment}{err_fragment})")
                next_heartbeat = now + heartbeat
            time.sleep(interval)
        elapsed = int(time.time() - start)
        raise RuntimeError(
            f"Timed out waiting for {name} at {base_url}{path} after {elapsed}s "
            f"(attempts={attempt}, last_status={last_status}, last_error={last_error})")

    def resolve_path(self, base_root: str, maybe_relative: Any) -> Path:
        p = Path(str(maybe_relative))
        if p.is_absolute():
            return p
        return Path(base_root) / p

    def normalize_base_path(self, path_value: Any) -> str:
        return _lib_normalize_base_path(path_value)

    def parse_service_url(self, url: Any, default_port: int) -> Any:
        return _lib_parse_service_url(url, default_port)

    def to_int(self, value: Any, fallback: Any = None) -> Any:
        return _lib_to_int(value, fallback=fallback)

    def coerce_list(self, value: Any) -> list:
        return _lib_coerce_list(value)

    def bool_cfg(self, cfg: Any, key: str, default: bool) -> bool:
        return _lib_bool_cfg(cfg, key, default)

    def env_truthy(self, name: str, default: bool = False) -> bool:
        return _lib_env_truthy(name, default=default)

    def load_bootstrap_default_json(self, filename: str, fallback: Any) -> Any:
        return _lib_load_json_default(BOOTSTRAP_DEFAULTS_DIR, filename, fallback, log=self.log)

    def deep_merge_objects(self, base_obj: Any, override_obj: Any) -> Any:
        if not isinstance(base_obj, dict):
            base_obj = {}
        if not isinstance(override_obj, dict):
            return json.loads(json.dumps(base_obj))
        out = json.loads(json.dumps(base_obj))
        for key, value in override_obj.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = self.deep_merge_objects(out.get(key), value)
                continue
            out[key] = json.loads(json.dumps(value))
        return out

    def field_map(self, field_list: Any) -> dict:
        out = {}
        for item in field_list or []:
            name = item.get("name")
            if not name:
                continue
            out[name] = item.get("value", "")
        return out

    def field_list(self, mapping: dict) -> list:
        return [{"name": key, "value": value} for key, value in mapping.items()]

    def normalize_token(self, value: Any) -> str:
        return _NORMALIZE_TOKEN_RE.sub("", str(value or "").strip().lower())

    def resolve_env_placeholder(self, value: Any) -> Any:
        if isinstance(value, str):
            raw = value.strip()
            match = _ENV_PLACEHOLDER_RE.fullmatch(raw)
            if match:
                return os.environ.get(match.group(1), "")
        return value

    def find_component_by_implementation(
        self, components: Any, implementation: Any,
    ) -> Any:
        target = str(implementation or "").strip()
        for component in components or []:
            if str((component or {}).get("implementation") or "").strip() == target:
                return component
        return None

    def _extract_level(self, msg: str) -> int:
        stripped = msg.lstrip()
        if stripped.startswith("["):
            bracket_end = stripped.find("]", 1)
            if bracket_end != -1:
                prefix = stripped[1:bracket_end].upper()
                return _PREFIX_TO_LEVEL.get(prefix, _INFO_LEVEL_VALUE)
        return _INFO_LEVEL_VALUE


_INSTANCE = RuntimePlatformService()
get_current_action_tag = _INSTANCE.get_current_action_tag
set_log_level = _INSTANCE.set_log_level
get_log_level = _INSTANCE.get_log_level
log = _INSTANCE.log
normalize_url = _INSTANCE.normalize_url
http_request = _INSTANCE.http_request
wait_for_service = _INSTANCE.wait_for_service
resolve_path = _INSTANCE.resolve_path
normalize_base_path = _INSTANCE.normalize_base_path
parse_service_url = _INSTANCE.parse_service_url
to_int = _INSTANCE.to_int
coerce_list = _INSTANCE.coerce_list
bool_cfg = _INSTANCE.bool_cfg
env_truthy = _INSTANCE.env_truthy
load_bootstrap_default_json = _INSTANCE.load_bootstrap_default_json
deep_merge_objects = _INSTANCE.deep_merge_objects
field_map = _INSTANCE.field_map
field_list = _INSTANCE.field_list
normalize_token = _INSTANCE.normalize_token
resolve_env_placeholder = _INSTANCE.resolve_env_placeholder
find_component_by_implementation = _INSTANCE.find_component_by_implementation
_extract_level = _INSTANCE._extract_level

__all__ = [
    "BOOTSTRAP_DEFAULTS_DIR",
    "RuntimePlatformService",
    "bool_cfg",
    "coerce_list",
    "current_action_tag",
    "env_truthy",
    "field_list",
    "field_map",
    "find_component_by_implementation",
    "deep_merge_objects",
    "get_current_action_tag",
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
