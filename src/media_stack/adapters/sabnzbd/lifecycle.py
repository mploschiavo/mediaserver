"""SABnzbd implementation of ``ServiceLifecycle``.

SABnzbd's lifecycle is structurally close to the *arr family: the
SAB process generates an API key in ``sabnzbd.ini`` on first start
(``[misc]`` section, ``api_key = <uuid>`` line). The canonical
reader is ``api.services.key_formats.read_ini``.

Differences from ServarrLifecycle:
  * INI format (``api_key_format=ini``) instead of XML.
  * Probe path is ``/sabnzbd/api?mode=version`` (the bare URL
    serves the dashboard; the API endpoint is what proves the
    service is actually answering with auth).
  * No URL-base reconciliation (SAB doesn't have a config knob
    equivalent to ``UrlBase``).

Same "wait for the file" mint semantic — if ``sabnzbd.ini`` doesn't
exist yet, the service is warming up and ``mint_api_key`` returns
``transient=True`` so the auto-heal cycle retries.
"""

from __future__ import annotations

import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)


logger = logging.getLogger(__name__)


_DEFAULT_HEALTH_PATH = "/sabnzbd/api?mode=version"
_DEFAULT_PROBE_TIMEOUT_SECONDS = 5
_DEFAULT_API_KEY_ENV = "SABNZBD_API_KEY"
_DEFAULT_API_KEY_FORMAT = "ini"


class SabnzbdLifecycle:
    """``ServiceLifecycle`` for SABnzbd. Stateless."""

    service_id: str = "sabnzbd"

    # --- probes -----------------------------------------------------

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
            "no api key in env or sabnzbd.ini",
            evidence={
                "env_var_checked": _api_key_env(ctx),
                "config_path": str(_config_path(ctx)),
            },
            evaluated_at=ctx.now(),
        )

    # --- discover ---------------------------------------------------

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

    # --- mint -------------------------------------------------------

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
                f"sabnzbd.ini not yet generated at {path}",
                transient=True,
                evidence={"config_path": str(path)},
            )
        return Outcome.failure(
            "sabnzbd.ini present but [misc] api_key= missing",
            transient=False,
            evidence={"config_path": str(path)},
        )

    # --- persist ----------------------------------------------------

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

    # --- helpers ----------------------------------------------------

    def _health_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        path = ctx.config.get("health_path") or _DEFAULT_HEALTH_PATH
        return f"{scheme}://{host}:{port}{path}"


# --- module helpers (private) ---------------------------------------

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


# Type-check at import.
_check: ServiceLifecycle = SabnzbdLifecycle()
del _check


__all__ = ["SabnzbdLifecycle"]
