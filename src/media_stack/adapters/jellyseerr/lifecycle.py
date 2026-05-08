"""Jellyseerr implementation of ``ServiceLifecycle``.

Jellyseerr stores its API key in ``settings.json`` under
``main.apiKey``. Same "wait for the file" mint shape as the *arr /
sabnzbd / bazarr family; the only difference is the JSON format.
The canonical reader is ``api.services.key_formats.read_json``.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from media_stack.adapters.jellyseerr.api_key_wiring import (
    JellyseerrApiKeyDiscoverableWirer,
)
from media_stack.adapters.jellyseerr.config_wiring import (
    JellyseerrConfigWirer,
)
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)


logger = logging.getLogger(__name__)


_DEFAULT_HEALTH_PATH = "/api/v1/status"
_DEFAULT_PROBE_TIMEOUT_SECONDS = 5
_DEFAULT_API_KEY_ENV = "JELLYSEERR_API_KEY"
_DEFAULT_API_KEY_FORMAT = "json"

# Stateless module-level singleton — the wirer is per-call parameterized
# by ctx (and, for arr-servers, the injected configure_handler +
# job_context_factory). Constructor-injected provider identity (slug /
# client id / scopes) keeps the magic-string surface in the wirer
# module rather than here.
_CONFIG_WIRER = JellyseerrConfigWirer()

# ADR-0005 Phase 5c.1 (wide) — api-key-discoverable wirer ports the
# legacy ``container_preflight_handlers`` shape into the
# orchestrator's promise model. Stateless module-level singleton —
# per-call parameterized by ``ctx``. ``probe`` covers env-or-disk
# discovery + optional ``GET /api/v1/auth/me`` validation; ``ensure``
# extends ``mint_api_key`` with env + k8s-secret persist so other
# promises in the same tick see the freshly-discovered key.
_API_KEY_DISCOVERABLE_WIRER = JellyseerrApiKeyDiscoverableWirer()


class JellyseerrLifecycle:
    service_id: str = "jellyseerr"

    def probe_running(self, ctx: OrchestrationContext) -> ProbeResult:
        url = self._health_url(ctx)
        if not url:
            return ProbeResult.failed(
                "no host/port in config — cannot probe",
                evidence={"config_keys": sorted(ctx.config.keys())},
                evaluated_at=ctx.now(),
            )
        try:
            with urllib.request.urlopen(
                url, timeout=_DEFAULT_PROBE_TIMEOUT_SECONDS,
            ) as resp:
                if resp.status == 200:
                    return ProbeResult.ok(
                        f"responsive at {url}",
                        evidence={"http_status": 200, "url": url},
                        evaluated_at=ctx.now(),
                    )
                return ProbeResult.failed(
                    f"non-200 from {url}: {resp.status}",
                    evidence={"http_status": resp.status, "url": url},
                    evaluated_at=ctx.now(),
                )
        except urllib.error.HTTPError as exc:
            return ProbeResult.failed(
                f"HTTP {exc.code} from {url}",
                evidence={"http_status": exc.code, "url": url},
                evaluated_at=ctx.now(),
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return ProbeResult.unknown(
                f"unreachable at {url}: {exc}",
                evidence={"url": url, "error": str(exc)},
                evaluated_at=ctx.now(),
            )

    def probe_has_api_key(self, ctx: OrchestrationContext) -> ProbeResult:
        key = self.discover_api_key(ctx)
        if key:
            return ProbeResult.ok(
                "api key discoverable",
                evidence={
                    "key_length": len(key),
                    "source": _classify_source(ctx, key),
                },
                evaluated_at=ctx.now(),
            )
        return ProbeResult.failed(
            "no api key in env or jellyseerr settings.json",
            evidence={
                "env_var_checked": _api_key_env(ctx),
                "config_path": str(_config_path(ctx)),
            },
            evaluated_at=ctx.now(),
        )

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None:
        env_var = _api_key_env(ctx)
        value = (ctx.secrets.get(env_var) or os.environ.get(env_var) or "").strip()
        if value:
            return value

        path = _config_path(ctx)
        if not path or not path.is_file():
            return None

        from media_stack.api.services.key_formats import READERS
        fmt = (ctx.config.get("api_key_format") or _DEFAULT_API_KEY_FORMAT).lower()
        reader = READERS.get(fmt)
        if reader is None:
            logger.debug("discover_api_key: no reader for format=%r", fmt)
            return None
        try:
            value = reader(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("discover_api_key: %s reader raised: %s", fmt, exc)
            return None
        return value.strip() if value else None

    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]:
        existing = self.discover_api_key(ctx)
        if existing:
            return Outcome.success(
                existing,
                attempts=0,
                evidence={"reason": "already_discoverable"},
            )

        path = _config_path(ctx)
        if not path:
            return Outcome.failure(
                "no api_key_config path in config — cannot mint",
                transient=False,
                evidence={"config_keys": sorted(ctx.config.keys())},
            )
        if not path.is_file():
            return Outcome.failure(
                f"jellyseerr settings.json not yet generated at {path}",
                transient=True,
                evidence={"config_path": str(path)},
            )
        return Outcome.failure(
            "jellyseerr settings.json present but main.apiKey missing",
            transient=False,
            evidence={"config_path": str(path)},
        )

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        env_var = _api_key_env(ctx)
        if not key:
            return Outcome.failure(
                "refusing to persist empty key",
                transient=False,
                evidence={"env_var": env_var},
            )
        os.environ[env_var] = key
        try:
            from media_stack.services.apps.core.job_adapters import (
                _persist_preflight_keys_to_secret_safe,
                _stub_state,
            )
            secret_result = _persist_preflight_keys_to_secret_safe(
                _stub_state(), {env_var: key},
            )
            return Outcome.success(
                evidence={
                    "env_written": env_var,
                    "secret_status": str(
                        (secret_result or {}).get("status") or secret_result,
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001
            return Outcome.failure(
                f"env written; secret patch failed: {exc}",
                transient=True,
                evidence={"env_written": env_var, "error": str(exc)},
            )

    # --- Jellyseerr config wiring (ADR-0005 Phase 3) ----------------
    #
    # Six methods delegate to ``JellyseerrConfigWirer`` in
    # ``config_wiring.py``. The lifecycle owns the api-key discovery
    # contract (above); the wirer owns the HTTP / settings.json /
    # restart shapes. The arr-servers ensurer takes the existing
    # ``configure_jellyseerr`` job handler + a ``JobContext``
    # factory because the underlying configuration flow is wide
    # enough (registry + library sync + restart) that re-implementing
    # it inside the wirer would duplicate ~200 lines of tested code.

    def probe_oidc(self, ctx: OrchestrationContext) -> ProbeResult:
        return _CONFIG_WIRER.probe_oidc(ctx)

    def ensure_oidc(self, ctx: OrchestrationContext) -> Outcome[None]:
        return _CONFIG_WIRER.ensure_oidc(ctx)

    def probe_application_url(self, ctx: OrchestrationContext) -> ProbeResult:
        return _CONFIG_WIRER.probe_application_url(ctx)

    def ensure_application_url(
        self, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return _CONFIG_WIRER.ensure_application_url(ctx)

    def probe_arr_servers(self, ctx: OrchestrationContext) -> ProbeResult:
        return _CONFIG_WIRER.probe_arr_servers(ctx)

    # --- API-key-discoverable wiring (ADR-0005 Phase 5c.1 wide) -----
    #
    # The ``jellyseerr-api-key-discoverable`` promise binds via
    # lifecycle dispatch through these two methods. ``probe`` matches
    # ``probe_has_api_key`` (env or settings.json) with optional
    # live HTTP validation; ``ensure`` reads from disk and persists
    # to env + k8s secret so other promises in the same tick see
    # the freshly-discovered value.

    def probe_api_key_discoverable(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _API_KEY_DISCOVERABLE_WIRER.probe(ctx)

    def ensure_api_key_discoverable(
        self, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return _API_KEY_DISCOVERABLE_WIRER.ensure(ctx)

    def ensure_arr_servers(
        self, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        # Lazy imports keep the lifecycle module light at load time
        # (the configure_jellyseerr handler pulls in Docker SDK,
        # k8s client, etc.) and break the import cycle that would
        # exist if the application layer imported the lifecycle.
        # Both imports go through the ``services/`` shim layer (the
        # same handler entry the legacy job runner resolves from
        # ``contracts/services/jellyseerr.yaml``) so the adapter
        # stays on the adapters/ → services/ side of the hexagon
        # ratchet — the application/ canonical module is reached
        # transitively via the shim, not by direct import here.
        from media_stack.services.apps.jellyseerr.configure_jellyseerr_job import (  # noqa: E501
            configure_jellyseerr,
        )
        from media_stack.services.jobs.framework import JobContext
        return _CONFIG_WIRER.ensure_arr_servers(
            ctx,
            configure_handler=configure_jellyseerr,
            job_context_factory=JobContext,
        )

    def _health_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        path = ctx.config.get("health_path") or _DEFAULT_HEALTH_PATH
        return f"{scheme}://{host}:{port}{path}"


def _api_key_env(ctx: OrchestrationContext) -> str:
    return (ctx.config.get("api_key_env") or _DEFAULT_API_KEY_ENV).strip()


def _config_path(ctx: OrchestrationContext) -> Path | None:
    rel = ctx.config.get("api_key_config")
    if not rel:
        return None
    config_root = (
        ctx.config.get("config_root")
        or ctx.extra.get("config_root")
        or os.environ.get("CONFIG_ROOT")
        or ""
    )
    return Path(config_root) / rel if config_root else Path(rel)


def _classify_source(ctx: OrchestrationContext, key: str) -> str:
    env_var = _api_key_env(ctx)
    if (ctx.secrets.get(env_var) or "").strip() == key:
        return "secrets"
    if os.environ.get(env_var, "").strip() == key:
        return "env"
    return "config_file"


_check: ServiceLifecycle = JellyseerrLifecycle()
del _check


__all__ = ["JellyseerrLifecycle"]
