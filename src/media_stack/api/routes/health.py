"""Health-domain GET routes (ADR-0007 Phase 1).

Proof-of-pattern for the OpenAPI-driven router. Phase 2 agents
copy this file's structure for their domain — see ADR-0007's
"Phase 2 agent-brief template".

Each method body is lifted verbatim from the legacy
``handlers_get.GetRequestHandler.handle()`` chain. The only change
is registration mechanism: ``@get(path)``-tagged class methods
instead of ``elif path == "..."`` branches. The router consults
this class's registrations BEFORE the legacy chain (see
``server.py``); registered routes win.

The legacy ``elif`` branches in ``handlers_get.py`` stay alive as
fallback during Phase 2 — a final cleanup commit after every
domain has migrated removes them.
"""

from __future__ import annotations

import time
from http import HTTPStatus
from typing import Any

from media_stack.api.cache import api_cache
from media_stack.api.routing import RouteModule, get
from media_stack.api.services import health as health_svc


class HealthGetRoutes(RouteModule):
    """All ``/healthz``, ``/readyz``, and ``/api/health*`` GET
    routes. The Router auto-discovers + instantiates this class
    + walks its tagged methods at startup."""

    @get("/healthz")
    def handle_healthz(self, handler: Any) -> None:
        """Liveness probe — 200 if the process is up.

        Used by k8s liveness probe + ``docker compose
        healthcheck``. Doesn't introspect downstream services; for
        that, see ``/api/health``.
        """
        handler._json_response(HTTPStatus.OK, {"status": "ok"})

    @get("/readyz")
    def handle_readyz(self, handler: Any) -> None:
        """Readiness probe — 200 with bootstrap-state metadata.

        Tells operators (and k8s readiness probe) whether the
        controller has finished its initial bootstrap pipeline.
        """
        handler._json_response(HTTPStatus.OK, {
            "status": "ready",
            "initial_bootstrap_done": handler.state.initial_bootstrap_done,
            "phase": handler.state.phase,
        })

    @get("/api/health")
    def handle_health(self, handler: Any) -> None:
        """Aggregated service-health probe.

        Probes every registered service in parallel + appends the
        result to the in-memory health history (which the dashboard
        reads via ``/api/health-history``).
        """
        result = health_svc.probe_services(api_cache)
        health_svc.append_health_history(result.get("services", {}))
        handler._json_response(HTTPStatus.OK, result)

    @get("/api/health-history")
    def handle_health_history(self, handler: Any) -> None:
        """Recent ``/api/health`` results, in time order.

        Drives the dashboard's "uptime trend" sparkline.
        """
        handler._json_response(
            HTTPStatus.OK, health_svc.get_health_history(),
        )

    @get("/api/ops/health")
    def handle_ops_health(self, handler: Any) -> None:
        """Aggregated runtime stats for the /ops dashboard tile.

        Replaces the UI-side ``Promise.resolve(...)`` stub that
        produced the "12/31/1969" bootstrap timestamp. See
        ``HealthService.get_ops_health`` for field semantics.
        """
        handler._json_response(HTTPStatus.OK, health_svc.get_ops_health())

    @get("/api/health/config-integrity")
    def handle_health_config_integrity(self, handler: Any) -> None:
        """Per-service config-file integrity check.

        Lists every service whose on-disk config doesn't match what
        the bootstrap pipeline expects (e.g. literal
        ``service_internal_url(...)`` text in unpackerr's TOML — the
        v1.0.150 root cause).
        """
        from media_stack.api.services import (
            config_integrity as integrity_svc,
        )
        handler._json_response(HTTPStatus.OK, {
            "services": integrity_svc.check_all(),
            "checked_at": time.time(),
        })

    @get("/api/health/crashloops")
    def handle_health_crashloops(self, handler: Any) -> None:
        """K8s crashloop detection.

        Lists registry services in CrashLoopBackOff plus
        non-registry pods (CronJobs, jellyfin-prewarm, anythingllm,
        etc.) so registry hygiene stays distinct from one-off pod
        noise.
        """
        from media_stack.api.services import crashloop as crashloop_svc
        handler._json_response(HTTPStatus.OK, {
            "services": crashloop_svc.check_all(),
            "non_registry_pods":
                crashloop_svc.list_non_registry_problem_pods(),
            "checked_at": time.time(),
        })

    @get("/api/health/stories")
    def handle_health_stories(self, handler: Any) -> None:
        """Live operator-friendly health summary.

        "Stories" — short narrative blurbs ("Jellyfin pod restarted
        3 times in the last hour", "Sonarr indexer count dropped
        from 12 to 4") composed from the in-memory health history.
        """
        from media_stack.api.services import (
            health_stories as stories_svc,
        )
        handler._json_response(HTTPStatus.OK, stories_svc.compose_live())


__all__ = ["HealthGetRoutes"]
