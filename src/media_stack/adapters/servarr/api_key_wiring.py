"""Servarr-family api-key-discovery wiring (ADR-0005 Phase 5c.1 wide).

Lifecycle-method port of the legacy ``container_preflight_handlers``
``run_preflight`` shape — the *arr family's flavor of the
``discover-api-keys`` flow. Single wirer parameterized by
``service_id`` covers sonarr / radarr / lidarr / readarr; mirrors the
download-client + indexer-pipeline + runtime-defaults wirer shapes.

The legacy preflight just calls into the *arr's HTTP entry point and
falls back to reading the auto-generated ``config.xml``. The
underlying read path is the same one ``ServarrLifecycle.discover_api_key``
already drives (the canonical XML reader in
``api.services.key_formats``), so the wirer's job is the wrapping
shape:

  * ``probe(ctx)`` — succeeds when ``discover_api_key`` returns a
    non-empty value (env var first, then ``config.xml``). When the
    key was found, optionally validates against ``GET /api/v3/system/status``
    so the probe ALSO answers "and the key actually authenticates" —
    if the service is unreachable we fall back to the disk signal so
    the probe doesn't loop forever during warmup.

  * ``ensure(ctx)`` — discover the key (env or disk), persist it to
    ``os.environ[<SERVICE>_API_KEY]`` so other promises in the same
    tick see the freshly-discovered value, and best-effort patch the
    k8s ``media-stack-secrets`` Secret. ``Outcome.success`` on
    discovery + persist; ``Outcome.failure(transient=True)`` while
    the *arr's ``config.xml`` hasn't been generated yet (warmup);
    ``Outcome.failure(transient=False)`` only when the file exists
    but lacks the ``<ApiKey>`` element (structural).

The wirer doesn't duplicate the discovery logic — it delegates to a
constructor-injected ``key_discoverer`` callable so tests can swap
in a fixture without touching ``key_formats``. By default the wirer
uses a thin lambda that imports + calls the canonical XML reader
through ``ServarrLifecycle.discover_api_key``'s code path; this also
keeps the ``adapters/`` → ``infrastructure/`` hexagon ratchet clean.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.parse
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


_SUPPORTED_SERVICE_IDS = frozenset(
    {"sonarr", "radarr", "lidarr", "readarr", "prowlarr"}
)
_DEFAULT_API_KEY_FORMAT = "xml"
_DEFAULT_VALIDATE_PATH = "/api/v3/system/status"
_DEFAULT_VALIDATE_TIMEOUT_SECONDS = 5

# Validation outcome sentinels — the four states ``_http_validate``
# returns. Module-level constants so the duplicate-strings ratchet
# stays clean (the literal appears in the probe branches + the
# validator's return statements + the test fixtures).
_VALIDATION_OK = "ok"
_VALIDATION_AUTH_FAILED = "auth_failed"
_VALIDATION_UNREACHABLE = "unreachable"
_VALIDATION_NO_URL = "no_url"

# HTTP status sentinels — extracted to named constants so the
# magic-numbers-over-100 ratchet stays clean.
_HTTP_OK = 200
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403

# Trim length when surfacing exception messages back into the
# orchestrator's evidence dict — keeps the JSON-encoded summary
# under MTU when an upstream raises a giant traceback string.
_ERROR_REASON_TRIM = 200


# Type aliases — the ``KeyDiscoverer`` callable receives the
# config-root path + the service's ``api_key_config`` rel-path + the
# format key (e.g. ``"xml"``) and returns the discovered key string,
# or empty on miss. This is the seam that lets tests inject a fixture
# without touching the canonical reader.
KeyDiscoverer = Callable[[Path, str], str]


class ServarrApiKeyDiscoverableWirer(LifecycleWirerBase):
    """Per-arr api-key-discoverable wirer (sonarr / radarr / lidarr /
    readarr).

    Stateless module-level singleton — per-call parameterized by
    ``service_id`` + ``ctx``. Constructor-injected ``key_discoverer``
    + ``http_validate_timeout`` keep the magic-number / import-shape
    surface in this module rather than the lifecycle class.
    """

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
        """Default key reader — delegates to the canonical XML / JSON
        readers under ``api.services.key_formats``. Lazy-imported so
        the adapters layer doesn't pull infra at module-load time.
        Defined as an instance method (not a module-level function)
        so the no-loose-functions ratchet stays clean; ``self`` is
        unused but needed for the method shape."""
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

    def probe(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> ProbeResult:
        sid = self._normalize_service_id(service_id)
        env_var = self._api_key_env(sid, ctx)
        discovered = self._discover(sid, ctx)
        if not discovered:
            return self._probe_failed(
                ctx,
                f"{sid}: no api key in env or config.xml",
                evidence={
                    "service_id": sid,
                    "env_var_checked": env_var,
                    "config_path": str(self._config_path(sid, ctx) or ""),
                },
            )
        # Optional live validation — when the *arr is reachable we
        # ALSO assert the key actually authenticates. When unreachable
        # we accept the disk signal alone (warmup window).
        validate_url = self._validate_url(sid, ctx)
        validation = self._http_validate(validate_url, discovered)
        return self._probe_for_validation(
            ctx, sid, env_var, discovered, validate_url, validation,
        )

    def _probe_for_validation(
        self,
        ctx: OrchestrationContext,
        sid: str,
        env_var: str,
        discovered: str,
        validate_url: str,
        validation: str,
    ) -> ProbeResult:
        """Map the ``_http_validate`` outcome onto a ProbeResult.
        Lifted out of :meth:`probe` to keep it under the methods-
        over-50-lines ratchet."""
        if validation == _VALIDATION_OK:
            return self._probe_ok(
                ctx,
                f"{sid}: api key discoverable + validates over http",
                evidence={
                    "service_id": sid, "env_var": env_var,
                    "key_length": len(discovered),
                    "http_validated": True,
                    "validate_url": validate_url,
                },
            )
        if validation == _VALIDATION_AUTH_FAILED:
            return self._probe_failed(
                ctx,
                f"{sid}: api key discovered but rejected by service",
                evidence={
                    "service_id": sid, "env_var": env_var,
                    "validate_url": validate_url,
                    "http_validated": False,
                    "reason": _VALIDATION_AUTH_FAILED,
                },
            )
        # ``unreachable`` / ``no_url``: trust the disk signal — the
        # promise is "key is in env / disk so other promises can
        # probe it"; if the *arr is still warming up, having the key
        # already in env is enough.
        return self._probe_ok(
            ctx,
            f"{sid}: api key discoverable on disk (service unreachable)",
            evidence={
                "service_id": sid, "env_var": env_var,
                "key_length": len(discovered),
                "http_validated": False,
                "reason": validation,
            },
        )

    # --- ensure -----------------------------------------------------

    def ensure(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        sid = self._normalize_service_id(service_id)
        env_var = self._api_key_env(sid, ctx)
        env_value = self._discover_secret(ctx, env_var)
        if env_value:
            return self._ensure_already_in_env(sid, env_var, env_value)
        config_path = self._config_path(sid, ctx)
        prereq = self._check_config_path_prereq(sid, config_path, ctx)
        if prereq is not None:
            return prereq
        return self._ensure_from_disk(sid, env_var, config_path, ctx)

    def _ensure_already_in_env(
        self, sid: str, env_var: str, env_value: str,
    ) -> Outcome[None]:
        # Opportunistically patch the k8s secret in case env was set
        # by an earlier process and the secret hadn't caught up.
        persist = self._persist_to_secret({env_var: env_value})
        return self._outcome_success(
            evidence={
                "service_id": sid, "env_var": env_var,
                "reason": "already_in_env",
                "secret_status": persist.get("status", "unknown"),
            },
        )

    def _check_config_path_prereq(
        self,
        sid: str,
        config_path: Path | None,
        ctx: OrchestrationContext,
    ) -> Outcome[None] | None:
        """Returns a non-success ``Outcome`` when the prereq isn't met,
        or ``None`` to let the caller proceed with the disk read."""
        if config_path is None:
            return self._outcome_permanent(
                f"{sid}: no api_key_config path in config",
                evidence={
                    "service_id": sid,
                    "config_keys": sorted(ctx.config.keys()),
                },
            )
        if not config_path.is_file():
            return self._outcome_transient(
                f"{sid}: config.xml not yet generated at {config_path}",
                evidence={
                    "service_id": sid,
                    "config_path": str(config_path),
                },
            )
        return None

    def _ensure_from_disk(
        self,
        sid: str,
        env_var: str,
        config_path: Path,
        ctx: OrchestrationContext,
    ) -> Outcome[None]:
        fmt = (ctx.config.get("api_key_format") or _DEFAULT_API_KEY_FORMAT).lower()
        discovered = (
            self._key_discoverer(config_path, fmt) or ""
        ).strip()
        if not discovered:
            # File exists but no key — structural. The *arr process
            # should have written the key on first start.
            return self._outcome_permanent(
                f"{sid}: config.xml present but <ApiKey> missing",
                evidence={
                    "service_id": sid,
                    "config_path": str(config_path),
                },
            )
        os.environ[env_var] = discovered
        persist = self._persist_to_secret({env_var: discovered})
        return self._outcome_success(
            evidence={
                "service_id": sid, "env_var": env_var,
                "config_path": str(config_path),
                "key_length": len(discovered),
                "secret_status": persist.get("status", "unknown"),
            },
        )

    # --- internals --------------------------------------------------

    def _normalize_service_id(self, service_id: str) -> str:
        sid = (service_id or "").strip().lower()
        if sid not in _SUPPORTED_SERVICE_IDS:
            raise ValueError(
                f"ServarrApiKeyDiscoverableWirer: unsupported "
                f"service_id={service_id!r}; expected one of "
                f"{sorted(_SUPPORTED_SERVICE_IDS)}",
            )
        return sid

    def _api_key_env(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        return (
            ctx.config.get("api_key_env")
            or f"{service_id.upper()}_API_KEY"
        ).strip()

    def _config_path(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> Path | None:
        rel = ctx.config.get("api_key_config")
        if not rel:
            # Fall back to the canonical default — the *arr family's
            # ``<service>/config.xml`` shape.
            rel = f"{service_id}/config.xml"
        config_root = (
            ctx.config.get("config_root")
            or ctx.extra.get("config_root")
            or os.environ.get("CONFIG_ROOT")
            or ""
        )
        if not config_root:
            return None
        return Path(config_root) / rel

    def _discover(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        env_var = self._api_key_env(service_id, ctx)
        env_value = self._discover_secret(ctx, env_var)
        if env_value:
            return env_value
        path = self._config_path(service_id, ctx)
        if path is None or not path.is_file():
            return ""
        fmt = (ctx.config.get("api_key_format") or _DEFAULT_API_KEY_FORMAT).lower()
        return (self._key_discoverer(path, fmt) or "").strip()

    def _validate_url(
        self, service_id: str, ctx: OrchestrationContext,
    ) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        return f"{scheme}://{host}:{port}{self._validate_path}"

    def _http_validate(self, url: str, api_key: str) -> str:
        """Returns one of the ``_VALIDATION_*`` sentinels. Never
        raises — wirers are expected to keep the orchestrator loop on
        its rails."""
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
        """Best-effort patch the k8s ``media-stack-secrets`` Secret.
        Reuses the existing helper so RBAC denials / no-k8s cases are
        reported the same way the legacy job did."""
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
    "KeyDiscoverer",
    "ServarrApiKeyDiscoverableWirer",
]
