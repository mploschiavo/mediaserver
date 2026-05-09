"""Bazarr implementation of ``ServiceLifecycle``.

Bazarr's auto-generated API key lands in
``bazarr/config/config.yaml`` as a top-level ``apikey:`` line on
first start. Same "wait for the file" mint shape as Sab/*arr; the
only difference is the YAML format. The canonical reader is
``api.services.key_formats.read_yaml``.

Honest-failure rule: probe failures are reported as ``failed`` /
``unknown`` per the Protocol; mint failures are typed (``transient``
vs structural). The lifecycle MUST NOT silently log-and-OK like the
legacy ``ensure-bazarr-language-profile`` did.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from media_stack.adapters.bazarr.config_wiring import BazarrConfigWirer
from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)


logger = logging.getLogger(__name__)


_DEFAULT_HEALTH_PATH = "/api/system/status"
_DEFAULT_PROBE_TIMEOUT_SECONDS = 5
_DEFAULT_API_KEY_ENV = "BAZARR_API_KEY"
_DEFAULT_API_KEY_FORMAT = "yaml"

# Stateless module-level singleton — the wirer is per-call parameterized
# by api_key + ctx, so one instance handles every Bazarr reconcile.
# Mirrors the ``_JELLYFIN_NOTIFIER_WIRER`` pattern in
# ``adapters/servarr/lifecycle.py``.
_BAZARR_CONFIG_WIRER = BazarrConfigWirer()


class BazarrLifecycle:
    service_id: str = "bazarr"

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
            "no api key in env or bazarr config.yaml",
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
                f"bazarr config.yaml not yet generated at {path}",
                transient=True,
                evidence={"config_path": str(path)},
            )
        return Outcome.failure(
            "bazarr config.yaml present but apikey: missing",
            transient=False,
            evidence={"config_path": str(path)},
        )

    # --- Config wiring (ADR-0005 Phase 3) ---------------------------
    #
    # Five probes + one ensurer all delegate to ``BazarrConfigWirer``
    # (in ``config_wiring.py``). The lifecycle owns the api-key
    # discovery contract; the wirer owns the HTTP / form-payload /
    # plugin-XML shape.
    #
    # All five promises share ``ensure_config_wiring`` — the legacy
    # ensurer's settings POST writes profile + toggles + providers +
    # arr-integration in one round-trip, so splitting into 5 ensurers
    # would be 5 redundant POSTs that each clobber the shared settings
    # document. The probes differ per-promise.

    def probe_language_profile(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _BAZARR_CONFIG_WIRER.probe_language_profile(
            self.discover_api_key(ctx), ctx,
        )

    def probe_default_profile_toggles(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _BAZARR_CONFIG_WIRER.probe_default_profile_toggles(
            self.discover_api_key(ctx), ctx,
        )

    def probe_providers(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _BAZARR_CONFIG_WIRER.probe_providers(
            self.discover_api_key(ctx), ctx,
        )

    def probe_arr_integration(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _BAZARR_CONFIG_WIRER.probe_arr_integration(
            self.discover_api_key(ctx), ctx,
        )

    def probe_jellyfin_plugin_config(
        self, ctx: OrchestrationContext,
    ) -> ProbeResult:
        return _BAZARR_CONFIG_WIRER.probe_jellyfin_plugin_config(ctx)

    def ensure_config_wiring(
        self, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return _BAZARR_CONFIG_WIRER.ensure(self.discover_api_key(ctx), ctx)

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


_check: ServiceLifecycle = BazarrLifecycle()
del _check


# ADR-0010 Phase 7 — module-level Job-handler alias the
# ``bazarr:ensure-config-wiring`` contract entry references. All
# six bazarr-* promises share this single Job (legacy ensurer's
# settings POST writes profile + toggles + providers + arr-
# integration in one round-trip; splitting would clobber the
# shared settings document). The Job's ``satisfies:`` list reflects
# all six promises.
from media_stack.domain.services.lifecycle_handler_adapter import (  # noqa: E402
    LifecycleHandlerAdapter,
)

ensure_config_wiring = LifecycleHandlerAdapter.bind(
    BazarrLifecycle, "ensure_config_wiring",
)


__all__ = ["BazarrLifecycle", "ensure_config_wiring"]
