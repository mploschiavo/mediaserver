"""Maintainerr implementation of ``ServiceLifecycle`` — ADR-0003 Phase 3.

Maintainerr is a *consumer* of other services' API keys (Jellyfin,
Radarr, Sonarr, Jellyseerr, Tautulli) — it doesn't have its own
discoverable API key for the controller to mint. Per the ADR design,
services without an API-key concept implement ``probe_has_api_key``
returning ``ProbeResult.ok("no api key concept")`` and the
mint/discover/persist methods returning ``Outcome.success`` with
``None``.

That keeps the orchestrator's loop uniform across every service —
no Optional methods, no per-service if-statements in the
orchestrator. The ``probe_running`` method still does real work; the
key-related methods are intentionally inert.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request

from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
    ServiceLifecycle,
)


logger = logging.getLogger(__name__)


_DEFAULT_HEALTH_PATH = "/app/maintainerr/api/settings"
_DEFAULT_PROBE_TIMEOUT_SECONDS = 5


class MaintainerrLifecycle:
    """``ServiceLifecycle`` for Maintainerr.

    Probe is real — Maintainerr's HTTP API is the operator's signal
    that the service is up. The key-related methods are no-ops with
    explanatory ``ProbeResult.ok`` / ``Outcome.success`` shapes.
    """

    service_id: str = "maintainerr"

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
        return ProbeResult.ok(
            "no api key concept (consumes upstream keys)",
            evaluated_at=ctx.now(),
        )

    def discover_api_key(self, ctx: OrchestrationContext) -> str | None:
        return None

    def mint_api_key(self, ctx: OrchestrationContext) -> Outcome[str]:
        return Outcome.success(
            None,  # type: ignore[arg-type]
            attempts=0,
            evidence={"reason": "no_api_key_concept"},
        )

    def persist_api_key(
        self, key: str, ctx: OrchestrationContext,
    ) -> Outcome[None]:
        return Outcome.success(
            evidence={"reason": "no_api_key_concept", "ignored_input": bool(key)},
        )

    def _health_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        path = ctx.config.get("health_path") or _DEFAULT_HEALTH_PATH
        return f"{scheme}://{host}:{port}{path}"


_check: ServiceLifecycle = MaintainerrLifecycle()
del _check


__all__ = ["MaintainerrLifecycle"]
