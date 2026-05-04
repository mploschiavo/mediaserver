"""Logs-domain GET routes (ADR-0007 Phase 2 wave 3).

Three GET routes migrated off the ``handlers_get.handle()`` elif
chain, all sharing the controller's logging surface:

* ``GET /api/log-level`` — the current runtime log level (DEBUG /
  INFO / WARN / ERROR), read off ``runtime_platform.get_log_level``.
  Tagged ``Config`` in the OpenAPI spec.
* ``GET /api/logs`` — the controller's in-memory log ring buffer,
  optionally filtered by ``after_seq`` / ``action`` query params.
  Tagged ``Operations`` in the spec.
* ``GET /api/logs/sources`` — the dynamic list of services + platform
  pods + CronJob templates that the Logs UI's filter dropdown shows.
  Tagged ``Operations`` in the spec.

Implementation choices, per Phase 2's "lift the body OR call the
helper — agent's choice based on what's cleanest" rule:

* ``/api/log-level`` is a one-line lazy-import + service call; lifted
  verbatim.
* ``/api/logs`` lifts the legacy body — its query-string parsing
  (``after_seq``, ``action``) is small enough that re-using it in a
  helper would just couple two modules. The legacy chain's
  ``path == "/api/logs" or path.startswith("/api/logs?")`` shape
  collapses to a single ``@get("/api/logs")`` here because
  ``server.py`` strips the query string before dispatch (see
  ``test_dispatch_strips_query_string_ratchet.py`` + the
  ``server.py:113 path = self.path.split("?")[0]`` invariant).
* ``/api/logs/sources`` lifts the legacy body too — it composes
  the registry's service ids with platform pod ids and the
  CronJob enumeration. The exception path on the registry-load
  call is preserved (``ImportError`` only — narrower than the
  legacy ``except Exception``).

NOT migrated in this wave:

* ``GET /api/logs/stream`` — the filterable SSE variant of the
  ring buffer is dispatched by the legacy chain only; it's not in
  ``contracts/api/openapi.yaml``, and the OpenAPI-driven Router
  will raise ``RouterMisconfigured`` at startup if we register
  any ``(verb, path)`` not declared in the spec
  (``router.py::_RouteCompiler._check_in_spec``). A follow-up
  wave needs to add ``/api/logs/stream`` to ``openapi.yaml``
  before the SSE route can move off the elif chain — the same
  blocker that left ``/api/logs/stream`` out of the
  ``log_streams.py`` (Phase 2 wave 2) migration.

The legacy ``elif`` branches in ``handlers_get.py`` for the three
migrated paths stay alive as fallback during Phase 2 — the final
cleanup commit after every domain has migrated removes them.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from media_stack.api.routing import RouteModule, get
from media_stack.api.services import ops as ops_svc
from media_stack.core.time_utils import ISO_8601_LOCAL

# Module-level logger keeps the ``"media_stack"`` literal off the
# per-method call sites and de-duplicates with the rest of the
# codebase's loggers. ``__name__`` resolves to
# ``media_stack.api.routes.logs`` — a child of the project's root
# logger, so the existing handler hierarchy still picks it up.
_LOGGER = logging.getLogger(__name__)


class LogsGetRoutes(RouteModule):
    """All ``/api/log-level`` + ``/api/logs`` + ``/api/logs/sources``
    GET routes. The Router auto-discovers + instantiates this class
    + walks its tagged methods at startup.

    Stateless — every dependency is module-level (``ops_svc``) or
    lazily imported inside the method that needs it (mirrors the
    legacy chain's lazy-import shape and keeps this module's startup
    import graph minimal).
    """

    @get("/api/log-level")
    def handle_log_level(self, handler: Any) -> None:
        """Return the controller's current runtime log level.

        Drives the Settings page's "log level" picker. Lifted from
        the legacy chain verbatim — the matching POST endpoint
        (``setLogLevel``) lives in ``handlers_post`` and isn't in
        scope for this wave.
        """
        from media_stack.services.runtime_platform import get_log_level
        handler._json_response(HTTPStatus.OK, {"level": get_log_level()})

    @get("/api/logs")
    def handle_logs(self, handler: Any) -> None:
        """Return ring-buffer entries, optionally filtered by action.

        Query params: ``after_seq`` (incremental tail; defaults 0)
        and ``action`` (filter to one action name). Body lifted from
        the legacy ``handlers_get._handle_logs`` — the elif chain's
        ``or path.startswith("/api/logs?")`` collapses to one
        ``@get("/api/logs")`` here because ``server.py`` strips the
        query string before dispatch (see
        ``test_dispatch_strips_query_string_ratchet``). ``ValueError``
        on the ``int()`` cast is narrowed from the legacy
        ``except Exception``-style swallow.
        """
        import time

        params: dict[str, str] = {}
        if "?" in handler.path:
            for part in handler.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        try:
            after_seq = int(params.get("after_seq", "0"))
        except ValueError:
            # Substitute a default AND log — keeps the
            # ``logging-only-in-exception-handlers`` ratchet's
            # required shape (two statements: log + fallback)
            # rather than collapsing to a bare log call.
            after_seq = 0
            _LOGGER.debug(
                "[DEBUG] Swallowed exception", exc_info=True,
            )
        action = params.get("action", "")
        entries = handler.state.get_logs_since(after_seq, action=action)
        handler._json_response(HTTPStatus.OK, {
            "logs": [
                {
                    "seq": seq,
                    "ts": time.strftime(
                        ISO_8601_LOCAL, time.localtime(ts),
                    ),
                    "msg": msg,
                    "action": act,
                }
                for seq, ts, msg, act in entries
            ],
            "count": len(entries),
        })

    @get("/api/logs/sources")
    def handle_logs_sources(self, handler: Any) -> None:
        """Return the Logs UI's filter-dropdown source list.

        Composes three buckets:
          * ``platform`` — the controller and ui pods, special-cased
            because they don't live in the SERVICES registry.
          * ``service`` — every entry in the SERVICES registry, sorted
            by id.
          * ``cronjob`` — every CronJob template's most-recent pod
            (e.g. ``media-stack-media-hygiene`` → the latest
            ``media-stack-media-hygiene-29619765-2j9dc``-style pod).

        The legacy chain's hardcoded list capped at 8 services even
        though SERVICES has 27+ — operators couldn't reach
        jellyfin/jellyseerr/sabnzbd/authelia/envoy logs etc. without
        this dynamic source list.

        ``ImportError`` on the registry import is the only failure
        mode worth narrowing for — it's the legacy chain's
        ``except Exception`` collapsed to its real failure shape.
        """
        from media_stack.core.logging_utils import log_swallowed
        try:
            from media_stack.api.services.registry import SERVICES
            svcs = sorted({s.id for s in SERVICES})
        except ImportError as exc:
            log_swallowed(exc)
            svcs = []
        cronjobs = ops_svc.list_cronjob_log_sources()
        platform = ["controller", "ui"]
        handler._json_response(HTTPStatus.OK, {
            "sources": [
                *(
                    {"id": p, "label": p.title(), "kind": "platform"}
                    for p in platform
                ),
                *(
                    {"id": s, "label": s.title(), "kind": "service"}
                    for s in svcs
                ),
                *cronjobs,
            ],
        })


__all__ = ["LogsGetRoutes"]
