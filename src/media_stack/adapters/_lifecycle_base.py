"""Shared base for ``ServiceLifecycle`` impls — ADR-0003 Phase 3c.

Most no-API-key services (homepage, envoy, flaresolverr, authelia,
authentik, maintainerr) share an identical lifecycle shape:

  * ``probe_running`` is real — HTTP GET against a health path.
  * ``probe_has_api_key`` returns ``ProbeResult.ok("no api key
    concept")``.
  * ``mint_api_key`` returns ``Outcome.success(None)``.
  * ``discover_api_key`` returns ``None``.
  * ``persist_api_key`` returns ``Outcome.success`` (no-op).

This base class provides that shape; concrete adapters are tiny:

    class HomepageLifecycle(NoApiKeyLifecycleBase):
        service_id = "homepage"

The contract YAML still names the concrete class (e.g.
``adapters.homepage.lifecycle:HomepageLifecycle``), so the orchestrator
+ ratchet keep their per-service granularity. The base just kills
~80 LOC of repetition per service.

The probe is identical to the per-service probes in Jellyfin /
Servarr / Sab / etc. — same tri-state semantics (200 = ok, other
HTTP = failed, network/timeout = unknown). Phase 4 may extract the
probe logic too once the orchestrator's needs are clearer.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from typing import ClassVar

from media_stack.domain.services import (
    OrchestrationContext,
    Outcome,
    ProbeResult,
)


logger = logging.getLogger(__name__)


_DEFAULT_PROBE_TIMEOUT_SECONDS = 5


class NoApiKeyLifecycleBase:
    """Base for services with no controller-discoverable API key.

    Subclasses MUST set ``service_id`` and MAY set
    ``_default_health_path``. Contract YAML's ``health_path`` field
    overrides the default at runtime.
    """

    service_id: ClassVar[str] = ""
    _default_health_path: ClassVar[str] = "/"

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
        return ProbeResult.ok(
            "no api key concept",
            evaluated_at=ctx.now(),
        )

    # --- key methods (intentionally inert) --------------------------

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

    # --- helpers ----------------------------------------------------

    def _health_url(self, ctx: OrchestrationContext) -> str:
        host = (ctx.config.get("host") or "").strip()
        port = ctx.config.get("port")
        if not host or not port:
            return ""
        scheme = (ctx.config.get("scheme") or "http").strip()
        path = ctx.config.get("health_path") or self._default_health_path
        return f"{scheme}://{host}:{port}{path}"


__all__ = ["NoApiKeyLifecycleBase"]
