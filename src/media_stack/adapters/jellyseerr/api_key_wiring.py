"""Jellyseerr api-key-discovery wiring (ADR-0005 Phase 5c.1 wide).

Single-service wirer for the Jellyseerr flavor of
``discover-api-keys``. Jellyseerr stores its admin API key in
``settings.json`` under ``main.apiKey`` rather than the *arr family's
``<ApiKey>X</ApiKey>`` config.xml. The canonical reader is
``api.services.key_formats.read_json``; this wirer wraps that read
in the orchestrator-friendly probe / ensure shape.

Lives next to ``config_wiring.py`` rather than inline in
``jellyseerr/lifecycle.py`` so the lifecycle module stays focused on
the core ``ServiceLifecycle`` Protocol surface (probe_running /
probe_has_api_key / mint_api_key / persist_api_key).

``JellyseerrApiKeyDiscoverableWirer`` owns:

  * ``probe(ctx)`` — succeeds when the key is in env or readable from
    ``settings.json``. Optional ``GET /api/v1/auth/me`` validation
    when the service is up + the key is in env: confirms the key is
    actually accepted by Jellyseerr (not just that the file says so).
    Falls back to the disk signal when the service is unreachable
    (warmup window).

  * ``ensure(ctx)`` — discover the key and persist to env +
    best-effort k8s secret. Tri-state outcome: success on discovery,
    transient on missing settings.json (warmup), permanent when the
    file exists but ``main.apiKey`` is empty (structural).
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from media_stack.adapters._shared.lifecycle_wirer_base import (
    LifecycleWirerBase,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


logger = logging.getLogger(__name__)


_DEFAULT_API_KEY_ENV = "JELLYSEERR_API_KEY"
_DEFAULT_API_KEY_FORMAT = "json"
_DEFAULT_API_KEY_CONFIG = "jellyseerr/settings.json"
_DEFAULT_VALIDATE_PATH = "/api/v1/auth/me"
_DEFAULT_VALIDATE_TIMEOUT_SECONDS = 5

# Validation outcome sentinels — see ``adapters/servarr/api_key_wiring.py``
# for the rationale (extract to constant so the duplicate-strings
# ratchet stays clean).
_VALIDATION_OK = "ok"
_VALIDATION_AUTH_FAILED = "auth_failed"
_VALIDATION_UNREACHABLE = "unreachable"
_VALIDATION_NO_URL = "no_url"

# HTTP status sentinels — see ``adapters/servarr/api_key_wiring.py``
# for the rationale. Extracted to named constants so the
# magic-numbers-over-100 ratchet stays clean.
_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403

# See ``adapters/servarr/api_key_wiring.py`` for the rationale on the
# error-message trim constant.
_ERROR_REASON_TRIM = 200


# Type alias — the ``KeyDiscoverer`` callable receives the absolute
# settings.json path + the format key (``"json"``) and returns the
# discovered key string or empty on miss. Lets tests inject a fixture
# without touching the canonical reader.
KeyDiscoverer = Callable[[Path, str], str]


class JellyseerrApiKeyDiscoverableWirer(LifecycleWirerBase):
    """Single-service wirer — Jellyseerr's settings.json read +
    env / k8s persist. Stateless module-level singleton; per-call
    parameterized by ``ctx``. Constructor-injected ``key_discoverer``
    + validation path keep the magic-string surface here."""

    def __init__(
        self,
        *,
        key_discoverer: KeyDiscoverer | None = None,
        validate_path: str = _DEFAULT_VALIDATE_PATH,
        validate_timeout_seconds: int = _DEFAULT_VALIDATE_TIMEOUT_SECONDS,
    ) -> None:
        self._key_discoverer: KeyDiscoverer = (
            key_discoverer or self._default_key_discoverer
        )
        self._validate_path = validate_path
        self._validate_timeout_seconds = validate_timeout_seconds

    def _default_key_discoverer(self, config_path: Path, fmt: str) -> str:
        """Default key reader — delegates to the canonical JSON reader
        under ``api.services.key_formats``. Lazy-imported so the
        adapters layer doesn't pull infra at module-load time. Defined
        as an instance method (not a module-level function) so the
        no-loose-functions ratchet stays clean."""
        del self  # unused — kept for method shape
        if not config_path.is_file():
            return ""
        try:
            from media_stack.api.services.key_formats import READERS
        except Exception as exc:  # noqa: BLE001
            logger.debug("key_formats unavailable: %s", exc)
            return ""
        reader = READERS.get(fmt)
        if reader is None:
            return ""
        try:
            value = reader(config_path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("key_formats reader raised: %s", exc)
            return ""
        return (value or "").strip()

    # --- probe ------------------------------------------------------

    def probe(self, ctx: OrchestrationContext) -> ProbeResult:
        env_var = self._api_key_env(ctx)
        discovered = self._discover(ctx)
        if not discovered:
            return self._probe_failed(
                ctx,
                "jellyseerr: no api key in env or settings.json",
                evidence={
                    "env_var_checked": env_var,
                    "config_path": str(self._config_path(ctx) or ""),
                },
            )
        validate_url = self._validate_url(ctx)
        validation = self._http_validate(validate_url, discovered)
        if validation == _VALIDATION_OK:
            return self._probe_ok(
                ctx,
                "jellyseerr: api key discoverable + validates over http",
                evidence={
                    "env_var": env_var,
                    "key_length": len(discovered),
                    "http_validated": True,
                    "validate_url": validate_url,
                },
            )
        if validation == _VALIDATION_AUTH_FAILED:
            return self._probe_failed(
                ctx,
                "jellyseerr: api key discovered but rejected by service",
                evidence={
                    "env_var": env_var,
                    "validate_url": validate_url,
                    "http_validated": False,
                    "reason": _VALIDATION_AUTH_FAILED,
                },
            )
        # ``unreachable`` / ``no_url`` — the disk signal is enough.
        return self._probe_ok(
            ctx,
            "jellyseerr: api key discoverable on disk (service unreachable)",
            evidence={
                "env_var": env_var,
                "key_length": len(discovered),
                "http_validated": False,
                "reason": validation,
            },
        )

    # --- ensure -----------------------------------------------------

    def ensure(self, ctx: OrchestrationContext) -> Outcome[None]:
        env_var = self._api_key_env(ctx)
        env_value = self._discover_secret(ctx, env_var)
        if env_value:
            persist = self._persist_to_secret({env_var: env_value})
            return self._outcome_success(
                evidence={
                    "env_var": env_var,
                    "reason": "already_in_env",
                    "secret_status": persist.get("status", "unknown"),
                },
            )

        config_path = self._config_path(ctx)
        if config_path is None:
            return self._outcome_permanent(
                "jellyseerr: no api_key_config path in config",
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not config_path.is_file():
            return self._outcome_transient(
                f"jellyseerr: settings.json not yet generated at {config_path}",
                evidence={"config_path": str(config_path)},
            )

        fmt = (ctx.config.get("api_key_format") or _DEFAULT_API_KEY_FORMAT).lower()
        discovered = (
            self._key_discoverer(config_path, fmt)
            or ""
        ).strip()
        if not discovered:
            return self._outcome_permanent(
                "jellyseerr: settings.json present but main.apiKey missing",
                evidence={"config_path": str(config_path)},
            )

        os.environ[env_var] = discovered
        persist = self._persist_to_secret({env_var: discovered})
        return self._outcome_success(
            evidence={
                "env_var": env_var,
                "config_path": str(config_path),
                "key_length": len(discovered),
                "secret_status": persist.get("status", "unknown"),
            },
        )

    # --- internals --------------------------------------------------

    def _api_key_env(self, ctx: OrchestrationContext) -> str:
        return (ctx.config.get("api_key_env") or _DEFAULT_API_KEY_ENV).strip()

    def _config_path(self, ctx: OrchestrationContext) -> Path | None:
        rel = ctx.config.get("api_key_config") or _DEFAULT_API_KEY_CONFIG
        config_root = (
            ctx.config.get("config_root")
            or ctx.extra.get("config_root")
            or os.environ.get("CONFIG_ROOT")
            or ""
        )
        if not config_root:
            return None
        return Path(config_root) / rel

    def _discover(self, ctx: OrchestrationContext) -> str:
        env_var = self._api_key_env(ctx)
        env_value = self._discover_secret(ctx, env_var)
        if env_value:
            return env_value
        path = self._config_path(ctx)
        if path is None or not path.is_file():
            return ""
        fmt = (ctx.config.get("api_key_format") or _DEFAULT_API_KEY_FORMAT).lower()
        return (self._key_discoverer(path, fmt) or "").strip()

    def _validate_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}{self._validate_path}"

    def _http_validate(self, url: str, api_key: str) -> str:
        if not url:
            return _VALIDATION_NO_URL
        if not api_key:
            return _VALIDATION_AUTH_FAILED
        request = urllib.request.Request(
            url, headers={"X-Api-Key": api_key},
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._validate_timeout_seconds,
            ) as resp:
                return (
                    _VALIDATION_OK if resp.status == _HTTP_OK
                    else _VALIDATION_AUTH_FAILED
                )
        except urllib.error.HTTPError as exc:
            if exc.code in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
                return _VALIDATION_AUTH_FAILED
            return _VALIDATION_UNREACHABLE
        except (urllib.error.URLError, OSError, TimeoutError):
            return _VALIDATION_UNREACHABLE

    def _persist_to_secret(self, payload: dict[str, str]) -> dict[str, Any]:
        try:
            from media_stack.services.apps.core.job_adapters import (
                _persist_preflight_keys_to_secret_safe,
                _stub_state,
            )
            result = _persist_preflight_keys_to_secret_safe(
                _stub_state(), payload,
            )
            return dict(result) if isinstance(result, dict) else {}
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "error",
                "reason": str(exc)[:_ERROR_REASON_TRIM],
            }


__all__ = [
    "JellyseerrApiKeyDiscoverableWirer",
    "KeyDiscoverer",
]
