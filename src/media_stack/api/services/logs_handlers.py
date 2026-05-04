"""Class-based GET-handler bodies for the logs / events feeds.

Lifted from ``media_stack.api.handlers_get`` during ADR-0007 Phase 2
Phase E (legacy-handler retirement).

This module owns the request-handler bodies that used to live as
``GetRequestHandler._handle_logs`` / ``_handle_service_logs`` /
``_handle_logs_sse`` / ``_handle_events_sse`` static methods on the
legacy dispatcher class. The pure filter / format predicates remain
in ``logs_sse.py`` -- this module wires them onto the
``BaseHTTPRequestHandler`` write surface.

OO discipline: every handler is an instance method on
:class:`LogsRequestHandlers`. The route modules construct one default
instance and call ``service.handle_xxx(handler)``; tests instantiate
their own to inject collaborators.
"""

from __future__ import annotations

import logging
import time
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs, unquote

from media_stack.core.logging_utils import log_swallowed

from .logs_sse import (
    compile_q,
    format_sse_event,
    should_emit_log_line,
)
from .ops import LOG_LINES_HARD_CAP, get_service_logs


class LogsRequestHandlers:
    """Bundles the four log-/event-feed GET handlers.

    Stateless service. The Router auto-discovers the route module
    that wraps these methods and calls them with the live
    ``ControllerAPIHandler``.
    """

    # --- /api/logs (in-memory ring buffer) --------------------------------

    def handle_logs(self, handler: Any) -> None:
        """Return log entries from the ring buffer, optionally filtered
        by action."""
        params: dict[str, str] = {}
        if "?" in handler.path:
            for part in handler.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        after_seq = 0
        try:
            after_seq = int(params.get("after_seq", "0"))
        except ValueError:
            logging.getLogger("media_stack").debug(
                "[DEBUG] Swallowed exception", exc_info=True,
            )
        action = params.get("action", "")
        entries = handler.state.get_logs_since(after_seq, action=action)
        handler._json_response(HTTPStatus.OK, {
            "logs": [
                {"seq": seq,
                 "ts": time.strftime(
                     "%Y-%m-%dT%H:%M:%S", time.localtime(ts),
                 ),
                 "msg": msg,
                 "action": act}
                for seq, ts, msg, act in entries
            ],
            "count": len(entries),
        })

    # --- /api/logs/{service} ---------------------------------------------

    def handle_service_logs(self, handler: Any, path: str) -> None:
        svc = path.split("/")[3]
        lines = 100
        since: str | None = None
        action: str | None = None
        level: str | None = None
        q: str | None = None
        include_previous = False
        if "?" in handler.path:
            qs = handler.path.split("?", 1)[1]
            params = parse_qs(qs, keep_blank_values=True)
            if "lines" in params:
                try:
                    raw_lines = int(params["lines"][0])
                    # Hard cap is the source of truth -- the dashboard
                    # exposes a picker up to 50k. Anything beyond is
                    # an operator typing nonsense or a bug.
                    lines = max(1, min(LOG_LINES_HARD_CAP, raw_lines))
                except ValueError:
                    logging.getLogger("media_stack").debug(
                        "[DEBUG] Swallowed exception", exc_info=True,
                    )
            if "since" in params:
                since = unquote(params["since"][0]) or None
            if "action" in params:
                action = unquote(params["action"][0]) or None
            if "level" in params:
                level = unquote(params["level"][0]) or None
            if "q" in params:
                q = unquote(params["q"][0]) or None
            if "previous" in params:
                include_previous = params["previous"][0].lower() in {
                    "1", "true", "yes",
                }
        handler._json_response(
            HTTPStatus.OK,
            get_service_logs(
                svc,
                lines=lines,
                since=since,
                action=action,
                level=level,
                q=q,
                include_previous=include_previous,
            ),
        )

    # --- /api/logs/stream (SSE) ------------------------------------------

    def handle_logs_sse(self, handler: Any) -> None:
        """Stream filtered controller log lines as Server-Sent Events.

        Same filter dimensions as ``GET /api/logs/{source}`` so the UI
        can fall back from SSE -> polling and keep the same query state.
        Closes cleanly on broken pipe / connection reset (the operator
        navigated away or the EventSource was disposed); other I/O
        errors are swallowed so a single bad client doesn't hang the
        loop.
        """
        params: dict[str, str] = {}
        if "?" in handler.path:
            qs = handler.path.split("?", 1)[1]
            parsed = parse_qs(qs, keep_blank_values=True)
            for k, vs in parsed.items():
                if vs:
                    params[k] = unquote(vs[0])

        try:
            after_seq = int(params.get("after_seq", "0"))
        except ValueError:
            after_seq = 0
        action_filter = params.get("action") or None
        level_filter = params.get("level") or None
        q_pattern = compile_q(params.get("q"))

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        try:
            while True:
                entries = handler.state.get_logs_since(after_seq)
                for seq, ts, msg, action_field, *_ in entries:
                    after_seq = seq
                    if not should_emit_log_line(
                        msg,
                        action_field,
                        action_filter=action_filter,
                        level_filter=level_filter,
                        q_pattern=q_pattern,
                    ):
                        continue
                    handler.wfile.write(
                        format_sse_event(seq, ts, msg, action_field),
                    )
                handler.wfile.flush()
                handler.state.wait_for_log(timeout=30.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            log_swallowed(BaseException("sse client disconnected"))

    # --- /api/events/stream (SSE) ----------------------------------------

    def handle_events_sse(self, handler: Any) -> None:
        """Stream typed domain events as Server-Sent Events.

        The handler subscribes a per-request ``queue.Queue`` to the
        process-wide ``EventBus`` and drains the queue into SSE
        frames on the wire. Disconnects (broken pipe / reset) tear
        down the subscription cleanly. A heartbeat comment frame
        every 25 seconds keeps reverse proxies (Envoy, nginx,
        Cloudflare) from idle-killing the connection.
        """
        from queue import Empty, Queue
        from media_stack.api.services.events_sse import (
            HEARTBEAT_FRAME,
            event_matches_topics,
            format_event_frame,
            parse_topics,
        )
        from media_stack.core.events import get_default_bus
        from media_stack.core.events.bus import Event

        params: dict[str, str] = {}
        if "?" in handler.path:
            qs = handler.path.split("?", 1)[1]
            parsed = parse_qs(qs, keep_blank_values=True)
            for k, vs in parsed.items():
                if vs:
                    params[k] = unquote(vs[0])
        topics = parse_topics(params.get("topics"))

        # Bounded queue so a stuck client can't consume unbounded
        # memory if the bus floods. 1000 events ~= 30s of bursty
        # traffic at our worst observed rate; if we ever fill up,
        # the bus handler drops the event silently rather than
        # blocking publishers.
        events_queue: "Queue[Event]" = Queue(maxsize=1000)

        def _on_event(ev: Event) -> None:
            try:
                events_queue.put_nowait(ev)
            except Exception:  # noqa: BLE001 - queue.Full + defensive
                log_swallowed(
                    BaseException("events queue full; dropping"),
                )

        bus = get_default_bus()
        sub = bus.subscribe_all(_on_event)

        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

        try:
            while True:
                try:
                    ev = events_queue.get(timeout=25.0)
                except Empty:
                    handler.wfile.write(HEARTBEAT_FRAME)
                    handler.wfile.flush()
                    continue
                if not event_matches_topics(ev, topics):
                    continue
                handler.wfile.write(format_event_frame(ev))
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            log_swallowed(BaseException("events sse client disconnected"))
        finally:
            bus.unsubscribe(sub)


# Module-level singleton so the route module's default fall-through
# can dispatch through a stable instance.
_logs_request_handlers = LogsRequestHandlers()


# Free-function aliases for the legacy import surface. These are thin
# bound-method references on the singleton instance.
_handle_logs = _logs_request_handlers.handle_logs
_handle_service_logs = _logs_request_handlers.handle_service_logs
_handle_logs_sse = _logs_request_handlers.handle_logs_sse
_handle_events_sse = _logs_request_handlers.handle_events_sse


__all__ = [
    "LogsRequestHandlers",
    "_logs_request_handlers",
    "_handle_logs",
    "_handle_service_logs",
    "_handle_logs_sse",
    "_handle_events_sse",
]
