"""Envoy-domain GET routes (ADR-0007 Phase 2 wave 3).

Four routes migrated off the legacy ``handlers_get.handle()`` elif
chain, all sharing the ``Metrics`` OpenAPI tag and powering the
operator-facing Routing tab on the dashboard:

* ``GET /api/envoy/stats`` — filtered Envoy admin-API counters
  (downstream/upstream connections, 2xx/4xx/5xx request totals).
* ``GET /api/envoy/admin-summary`` — operator-friendly aggregate of
  cluster health, request rates, p50/p95/p99 latency, active
  connections, and TLS handshake errors. Each call also appends a
  sample to the rolling buffer that backs ``timeseries``.
* ``GET /api/envoy/access-log?limit=N`` — last N parsed entries
  from Envoy's access log. ``limit`` is bounded to ``[1, 500]``;
  the default is ``50``.
* ``GET /api/envoy/timeseries?window=S`` — rolling buffer of recent
  ``admin-summary`` samples plus derived rate deltas. ``window``
  defaults to 1800s (30 min) and clamps to >= 60s in the service
  layer.

Implementation notes:

* All four method bodies are lifted verbatim from the legacy
  ``handlers_get`` chain. The only change is the registration
  mechanism: ``@get(path)``-tagged class methods rather than
  ``elif path == "..."`` branches. The Router consults this
  module's registrations BEFORE the legacy chain (see
  ``server.py``); the legacy ``elif`` branches stay alive only as
  fallback during Phase 2.
* The two query-string-bearing routes (``access-log``,
  ``timeseries``) parse ``handler.path`` directly — production
  ``server.py`` strips the query off the dispatch ``path`` but
  leaves ``handler.path`` intact, so ``parse_qs(urlparse(
  handler.path).query)`` continues to work as it did in the legacy
  chain.
* The ``access-log`` body narrows the legacy ``except Exception:
  # noqa: BLE001`` to ``except (OSError, RuntimeError, ValueError)
  as exc`` — these are the actual failure modes for the access-log
  tailer (file-system, subprocess, parsing). A catch-all there
  would shadow programmer errors during future refactors;
  out-of-band exceptions still bubble up to the controller's
  top-level guard, which is the right behaviour for unexpected
  errors.
* ``tail_envoy_access_log`` is imported at module level rather
  than lazily — the legacy chain's lazy import predated the
  Router's auto-discovery and was load-shedding for the legacy
  monolith. Inside a focused route module the import graph is
  already minimal, so deferred imports would only contribute
  noise to the ``CIRCULAR_IMPORT_RISK`` ratchet.

Pattern: **Adapter** — each route method adapts the legacy
service-call shape ("call a free function on the metrics service,
hand the dict to ``_json_response``") onto the Router's
class-method registration surface.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, urlparse

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import metrics as metrics_svc
from media_stack.api.services.envoy_access_log import (
    tail_envoy_access_log,
)


# Defaults match the legacy chain; centralising them here means
# tests + future tuning have one place to look. Not pulled into
# ``core.defaults`` because they're route-local clamp values, not
# environment-derived configuration.
_ACCESS_LOG_DEFAULT_LIMIT = 50
_ACCESS_LOG_MIN_LIMIT = 1
_ACCESS_LOG_MAX_LIMIT = 500
_TIMESERIES_DEFAULT_WINDOW_S = 1800


class EnvoyGetRoutes(RouteModule):
    """Metrics-tag GET routes for the Envoy edge-router operator
    panel. The Router auto-discovers + instantiates this class +
    walks its tagged methods at startup."""

    @get("/api/envoy/stats")
    def handle_envoy_stats(self, handler: Any) -> None:
        """Return filtered Envoy admin-API counters.

        Drives the Routing tab's stats summary card. The service
        layer fetches from Envoy's ``/stats?format=json`` admin
        endpoint and filters to the request-volume gauges most
        useful for triage.
        """
        handler._json_response(
            HTTPStatus.OK, metrics_svc.get_envoy_stats(),
        )

    @get("/api/envoy/admin-summary")
    def handle_envoy_admin_summary(self, handler: Any) -> None:
        """Return the operator-facing aggregate of cluster health,
        request rates, p50/p95/p99 latency, active connections, and
        TLS handshake errors. Surfaced on the Routing tab.

        Side effect: each call appends a sample to the rolling
        buffer consumed by ``/api/envoy/timeseries``, so the panel's
        sparkline reflects only the time the panel has been open.
        """
        handler._json_response(
            HTTPStatus.OK, metrics_svc.get_envoy_admin_summary(),
        )

    @get("/api/envoy/access-log")
    def handle_envoy_access_log(self, handler: Any) -> None:
        """Stream the last N lines of Envoy's access log so the
        operator panel can show live request flow with source IPs,
        paths, statuses, upstream cluster, and latency.

        Sources tried in order (delegated to
        ``tail_envoy_access_log``):

        1. ``ENVOY_ACCESS_LOG_PATH`` env var if set (file path).
        2. ``kubectl logs`` for the envoy pod (when running on K8s
           — the controller's ServiceAccount has read access).
        3. ``docker compose logs`` for the envoy service when no
           kubectl is available.

        Each entry is parsed as JSON when possible (the Envoy
        access_log filter is configured to emit JSON in the default
        media-stack profile); falls back to raw text otherwise.

        ``limit`` is clamped to ``[_ACCESS_LOG_MIN_LIMIT,
        _ACCESS_LOG_MAX_LIMIT]`` to bound response size.
        """
        qs = parse_qs(urlparse(handler.path).query)
        try:
            limit = int(
                (qs.get("limit") or [str(_ACCESS_LOG_DEFAULT_LIMIT)])[0],
            )
        except (TypeError, ValueError):
            limit = _ACCESS_LOG_DEFAULT_LIMIT
        limit = max(
            _ACCESS_LOG_MIN_LIMIT, min(_ACCESS_LOG_MAX_LIMIT, limit),
        )
        try:
            rows = tail_envoy_access_log(limit=limit)
            handler._json_response(HTTPStatus.OK, {
                "rows": rows,
                "limit": limit,
            })
        except (OSError, RuntimeError, ValueError) as exc:
            # Narrowed from the legacy ``except Exception:`` to the
            # actual failure modes documented for the access-log
            # tailer: file-system errors (OSError), subprocess /
            # geoip lookup failures (RuntimeError), or malformed
            # input (ValueError). A broader catch would shadow
            # programmer errors during future refactors.
            handler._json_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc)[:200], "rows": []},
            )

    @get("/api/envoy/timeseries")
    def handle_envoy_timeseries(self, handler: Any) -> None:
        """Return the rolling buffer of recent admin-summary
        samples plus derived rate deltas.

        The buffer is populated as a side-effect of admin-summary
        polling, so the series only covers the time the Routing
        panel has been open. The ``window`` query param defaults to
        ``_TIMESERIES_DEFAULT_WINDOW_S`` (1800s == 30 min); the
        service layer clamps to >= 60s.
        """
        qs = parse_qs(urlparse(handler.path).query)
        try:
            window = int(
                (qs.get("window") or [str(_TIMESERIES_DEFAULT_WINDOW_S)])[0],
            )
        except (TypeError, ValueError):
            window = _TIMESERIES_DEFAULT_WINDOW_S
        handler._json_response(
            HTTPStatus.OK,
            metrics_svc.get_envoy_timeseries(window),
        )


__all__ = ["EnvoyGetRoutes"]
