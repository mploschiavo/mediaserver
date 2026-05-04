"""Log-stream GET routes (ADR-0007 Phase 2).

Two routes migrated off the ``handlers_get.handle()`` elif chain:

* ``GET /logs/stream`` — the legacy unfiltered Server-Sent Events
  stream of controller log lines. Just calls
  ``handler._sse_response()``; the streaming loop, filter parsing
  and broken-pipe handling all live on the controller-API handler
  itself. The newer filterable variant lives at
  ``/api/logs/stream`` and is still served via the legacy chain
  (different domain — Operations rather than State — and not in
  scope for this migration).
* ``GET /api/logs/{service}`` — recent log lines from a single
  service's container/pod. Parameterized — the spec declares
  ``service`` (snake_case, lower-case) as the path parameter, so
  the handler kwarg matches the spec verbatim.

Implementation choice, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* ``/logs/stream`` lifts the body: it's a single line of code
  (``handler._sse_response()``) so there's no helper to call and
  nothing to share with the legacy chain.
* ``/api/logs/{service}`` delegates to the legacy
  ``GetRequestHandler._handle_service_logs`` helper. That helper
  re-parses ``handler.path`` to pull the service id back out of
  the URL — wasteful now that the Router already extracted it as
  a kwarg, but lifting the ~50-LoC body would also lift the
  query-string parsing for ``lines`` / ``since`` / ``action`` /
  ``level`` / ``q`` / ``previous`` and the
  ``LOG_LINES_HARD_CAP`` clamping. A future cleanup commit can
  collapse the helper into this module once the legacy chain is
  deleted; for now we pass the parameterized ``service`` kwarg
  back into the helper's ``path`` argument by reconstructing the
  ``/api/logs/<service>`` form so the helper's ``path.split``
  still finds it.

The SSE route's response shape is intentionally not assertable
in unit tests beyond status + content-type — the actual streaming
happens against the real socket, which the
``MockControllerHandler._sse_response`` stub deliberately doesn't
emulate. The integration coverage for SSE lives elsewhere; here
we just pin "Router dispatched to the SSE branch."
"""

from __future__ import annotations

from typing import Any

from media_stack.api.services.logs_handlers import _handle_service_logs
from media_stack.api.routing import RouteModule, get


class LogStreamsGetRoutes(RouteModule):
    """Log-stream GET routes. The Router auto-discovers and
    instantiates this class at startup, then walks tagged methods
    for registration."""

    @get("/logs/stream")
    def handle_log_stream(self, handler: Any) -> None:
        """Open the legacy unfiltered SSE log stream.

        Defers all work to the controller-API handler's
        ``_sse_response``: it owns the streaming loop, the
        broken-pipe / connection-reset handling, and the
        ``after_seq`` resume parameter parsing. The migration
        moves the dispatch decision (Router vs. ``elif`` chain)
        without changing what bytes go on the wire.
        """
        handler._sse_response()

    @get("/api/logs/{service}")
    def handle_service_logs(self, handler: Any, service: str) -> None:
        """Return the most-recent log lines for one service.

        ``service`` is bound by the Router from the path segment
        per the OpenAPI ``parameters: [{name: service, in: path}]``
        declaration. The legacy
        ``GetRequestHandler._handle_service_logs`` helper pulls
        the id back out of ``path`` itself, so we hand it the
        canonical ``/api/logs/<service>`` form here — the helper's
        query-string parsing still reads off ``handler.path``,
        which the Router leaves untouched.
        """
        _handle_service_logs(handler, f"/api/logs/{service}")


__all__ = ["LogStreamsGetRoutes"]
