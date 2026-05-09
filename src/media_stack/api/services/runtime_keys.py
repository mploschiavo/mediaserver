"""Runtime API-key resolution with env→file fallback and per-process caching.

Why this module exists
----------------------

The bootstrap controller's process bakes API keys into ``os.environ`` only
after it parses each service's on-disk config file (``config.xml``,
``Server.xml``, etc.). The long-running API server process is a *different*
Python process — it inherits ``os.environ`` from the K8s
``media-stack-secrets`` Secret. On a fresh install (or any time a service
rotates its key in the UI), that Secret can be empty for some entries,
which makes endpoints like ``/api/libraries`` and ``/api/recent`` skip the
upstream call and return empty payloads (LibraryStatsTiles renders 1 of
each).

This helper bridges the gap. It looks up the key in this order:

1. ``os.environ[<SVC>_API_KEY]`` if set + non-empty.
2. Read the on-disk config file for the service (registry-driven —
   reuses the same parsers the bootstrap uses).
3. Returns ``None`` if neither source has a key, so callers can degrade
   gracefully instead of silently sending empty-string credentials.

A 30-second per-process cache avoids hot-path file reads when the helper
is called dozens of times per request (e.g. analytics sweeping every arr).

The cache is process-local and time-bounded — when a service rotates its
key, the new value is picked up within 30s without needing a controller
restart.

Public API
----------

- ``read_service_api_key(service: str) -> str | None``
- ``invalidate_cache(service: str | None = None) -> None``
- ``services_missing_keys() -> list[str]`` — for ``/api/health/stories``
  rule that surfaces failed discoveries to operators.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from media_stack.core.logging_utils import log_swallowed


# Cache TTL — 30s is short enough that key rotations are picked up quickly,
# long enough that hot paths (analytics looping over every arr) don't
# repeatedly stat() the config files.
_CACHE_TTL_SECONDS = 30.0

# Per-process cache: ``service_id -> (value_or_None, expiry_ts)``.
# ``None`` is a legitimate cached value — caching the *absence* of a key
# avoids re-parsing every request when a service genuinely has no key
# configured yet.
_CACHE: dict[str, tuple[Optional[str], float]] = {}
_CACHE_LOCK = threading.Lock()


def _now() -> float:
    """Indirection for tests so they can fast-forward time."""
    return time.monotonic()


def _read_env_key(service_id: str) -> str:
    """Read ``<SVC>_API_KEY`` from the environment.

    Uses the registry's declared ``api_key_env`` field when available,
    falling back to the conventional ``<UPPER_ID>_API_KEY`` for services
    that don't declare one. Treats the legacy
    ``replace-after-first-boot`` placeholder as "not set" — the bootstrap
    used to leave it in the env when no real key had been issued yet,
    and downstream callers should not send it as a credential.
    """
    env_var: str = ""
    try:
        from media_stack.core.service_registry.registry import SERVICE_MAP

        svc = SERVICE_MAP.get(service_id)
        if svc and svc.api_key_env:
            env_var = svc.api_key_env
    except Exception as exc:
        log_swallowed(exc)
    if not env_var:
        env_var = f"{service_id.upper()}_API_KEY"
    val = (os.environ.get(env_var) or "").strip()
    if not val or val.lower() == "replace-after-first-boot":
        return ""
    return val


def _read_file_key(service_id: str) -> str:
    """Read the API key from the service's on-disk config file.

    Delegates to the registry's format-aware reader (``read_xml`` for the
    *arr family, ``read_json`` for Jellyseerr, etc.). Jellyfin is special-
    cased because its key lives in SQLite — we use the same DB reader the
    bootstrap uses so the path is identical.
    """
    config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
    try:
        from media_stack.core.service_registry.registry import read_api_key_from_file

        key = read_api_key_from_file(service_id, str(config_root))
        if key:
            return key
    except Exception as exc:
        log_swallowed(exc)

    # Jellyfin stores its key in SQLite, not a config file. The registry's
    # readers cover this via ``api_key_format: sqlite`` only when the
    # contract sets ``api_key_config`` to the DB path; for older contracts
    # we fall back to the dedicated DB helper.
    if service_id == "jellyfin":
        try:
            from media_stack.services.apps.jellyfin.api_key_db import (
                read_jellyfin_api_key_from_db,
            )

            jf_cfg = {
                "api_key_db_path": "jellyfin/data/jellyfin.db",
                "api_key_name_preference": [
                    "Jellyfin",
                    "Jellyseerr",
                    "media-stack-controller",
                ],
            }
            token, _src = read_jellyfin_api_key_from_db(
                str(config_root),
                jf_cfg,
                coerce_list=lambda v: list(v)
                if isinstance(v, (list, tuple))
                else [v],
                resolve_path=lambda root, rel: Path(root) / rel,
            )
            if token:
                return str(token).strip()
        except Exception as exc:
            log_swallowed(exc)
    return ""


def read_service_api_key(service: str) -> Optional[str]:
    """Return the API key for ``service`` or ``None`` if neither env nor
    the on-disk config file has one.

    Returns the env value if non-empty, otherwise the on-disk value, or
    ``None`` to signal "callers should surface a clear error instead of
    sending an empty credential".

    The 30-second per-process cache means a key rotation propagates
    automatically; tests can call ``invalidate_cache()`` to bypass it.
    """
    if not service:
        return None
    now = _now()
    with _CACHE_LOCK:
        cached = _CACHE.get(service)
        if cached is not None and cached[1] > now:
            return cached[0]

    env_val = _read_env_key(service)
    if env_val:
        result: Optional[str] = env_val
    else:
        file_val = _read_file_key(service)
        result = file_val if file_val else None

    with _CACHE_LOCK:
        _CACHE[service] = (result, now + _CACHE_TTL_SECONDS)
    return result


def invalidate_cache(service: Optional[str] = None) -> None:
    """Drop cached entries.

    With ``service=None`` clears the whole cache. Used by tests and by
    the ``discover-api-keys`` job when it patches the K8s Secret — the
    next API request should pick up the freshly-written value rather
    than wait for the TTL to lapse.
    """
    with _CACHE_LOCK:
        if service is None:
            _CACHE.clear()
            return
        _CACHE.pop(service, None)


def services_missing_keys() -> list[str]:
    """Return registry service IDs that have ``api_key_env`` declared but
    neither env nor on-disk config yields a key.

    Used by ``/api/health/stories`` to surface a "discover-api-keys
    failed for: <list>" warning instead of letting endpoints silently
    return empty payloads. Cheap because it goes through the cache —
    repeated calls during one render don't refault the disk.
    """
    missing: list[str] = []
    try:
        from media_stack.core.service_registry.registry import SERVICES
    except Exception as exc:
        log_swallowed(exc)
        return missing
    for svc in SERVICES:
        if not getattr(svc, "api_key_env", ""):
            continue
        # Skip profile-gated services that aren't enabled — they
        # legitimately have no key on a deploy that doesn't include
        # them, and surfacing them would be noise.
        try:
            from media_stack.core.service_registry.registry import is_service_enabled

            if not is_service_enabled(svc):
                continue
        except Exception as exc:
            log_swallowed(exc)
        if read_service_api_key(svc.id) is None:
            missing.append(svc.id)
    return missing
