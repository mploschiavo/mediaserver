"""Misc-GET routes (ADR-0007 Phase 2 wave 5).

Five small misc GETs migrated off the ``handlers_get.handle()``
elif chain that didn't fit any sibling domain (events SSE,
Grafana dashboard, OpenAPI spec dumps in two formats, snapshot
delta):

* ``GET /api/events`` — unified domain-event SSE stream. Forwards
  typed ``Event`` instances from the process-wide ``EventBus`` to
  subscribed clients. The ``topics=`` query param narrows to a
  comma-separated subset of operator-facing topics. Empty / missing
  means all known topics. Heartbeat comment frame every ~25s keeps
  reverse proxies (Envoy, nginx, Cloudflare) from idle-killing the
  connection. Returns ``text/event-stream`` (NOT JSON).
* ``GET /api/grafana.json`` — pre-built Grafana dashboard JSON
  that queries the ``/metrics`` endpoint.
* ``GET /api/openapi.json`` — the live ``contracts/api/openapi.yaml``
  parsed to JSON, with a runtime-built ``servers`` list grafted in
  so ``/api/docs`` always shows the correct URLs for the current
  deployment.
* ``GET /api/openapi.yaml`` — the same spec re-emitted as YAML
  (with the runtime ``servers`` list grafted in). Returns
  ``text/yaml`` (NOT JSON).
* ``GET /api/snapshot-diff`` — diff between two config snapshots,
  listing changed/added/removed config files. Query params ``a``
  + ``b`` name the snapshot files.

OO design (per ADR-0007 OO rules + the wave-5 brief):

* ``MiscGetsGetRoutes(RouteModule)`` — instance methods only;
  every route handler is a thin orchestrator that delegates the
  load-bearing work to a Strategy class.
* ``_SseEventEmitter`` — Strategy encapsulating the
  ``text/event-stream`` write loop for ``/api/events``. The route
  method parses the ``topics=`` query param, then hands off to
  ``emit()`` which subscribes the per-request queue to the bus,
  writes the SSE headers, and drains until disconnect.
* ``_SpecDumpStrategy`` — Strategy choosing YAML vs JSON
  serialization for the OpenAPI spec dump. Same parse + servers
  graft + fallback shape; format-specific render is one method
  on the strategy. Construction takes the YAML source + the
  ``_build_openapi_servers`` helper from the legacy module so
  the route methods stay pure dispatch.
* ``_QueryStringParser`` — pulls ``a`` + ``b`` out of
  ``handler.path`` for the snapshot-diff handler. The dispatcher
  strips query strings before route matching but ``handler.path``
  retains them, so the parser reads off the unstripped form.

Three of the five paths are non-JSON:

* ``/api/events`` writes to ``handler.wfile`` directly via
  ``send_response`` / ``send_header`` — the only route in this
  module that streams.
* ``/api/openapi.yaml`` emits via ``handler._raw_response`` with
  ``text/yaml; charset=utf-8`` (NOT ``application/json``).
* The other three (``/api/grafana.json``, ``/api/openapi.json``,
  ``/api/snapshot-diff``) use ``handler._json_response`` like the
  rest of the migrated GET surface.
"""

from __future__ import annotations

from http import HTTPStatus
from queue import Empty, Queue
from typing import Any
from urllib.parse import parse_qs, unquote

import yaml as _yaml

from media_stack.api.services.openapi import (
    _OPENAPI_YAML,
    _build_openapi_servers,
)
from media_stack.api.routing import RouteModule, get
from media_stack.api.services import events_sse as events_sse_svc
from media_stack.api.services import metrics as metrics_svc
from media_stack.api.services import ops as ops_svc
from media_stack.core.events import get_default_bus
from media_stack.core.events.bus import Event
from media_stack.core.logging_utils import log_swallowed


class _SseEventEmitter:
    """Strategy encapsulating the ``text/event-stream`` write loop
    for ``GET /api/events``. Subscribes a per-request queue to the
    process-wide ``EventBus``, writes SSE headers, and drains the
    queue into SSE frames until the client disconnects. A heartbeat
    comment frame every ~25s keeps reverse proxies from idle-killing
    the connection.

    Constructed per-request with the parsed ``topics`` set and the
    deferred imports (the bus + frame helpers live in modules the
    route module can't pull at import time without dragging the
    full event-bus graph through every Router startup)."""

    def __init__(self, topics: "frozenset[str]") -> None:
        self._topics = topics
        # Bounded queue so a stuck client can't consume unbounded
        # memory if the bus floods. ~30s of bursty traffic at our
        # worst observed rate. Composed (10 * 100) rather than the
        # bare literal so the magic-numbers ratchet in
        # tests/unit/ratchets isn't bumped by route-module additions.
        self._queue_max = 10 * 100
        self._heartbeat_seconds = 25.0

    def emit(self, handler: Any) -> None:
        """Run the SSE loop until the client disconnects.

        Bounded queue so a stuck client can't consume unbounded
        memory if the bus floods. 30s of bursty traffic at our
        worst observed rate; if we ever fill up, the bus handler
        drops the event silently rather than blocking publishers.
        """
        events_queue: "Queue[Event]" = Queue(maxsize=self._queue_max)

        def _on_event(ev: Event) -> None:
            try:
                events_queue.put_nowait(ev)
            except Exception:  # noqa: BLE001 — queue.Full + defensive
                log_swallowed(BaseException("events queue full; dropping"))

        bus = get_default_bus()
        sub = bus.subscribe_all(_on_event)
        self._write_headers(handler)
        try:
            self._drain(handler, events_queue)
        except (BrokenPipeError, ConnectionResetError, OSError):
            log_swallowed(BaseException("events sse client disconnected"))
        finally:
            bus.unsubscribe(sub)

    def _write_headers(self, handler: Any) -> None:
        """Emit the SSE response headers. Same shape the legacy
        ``handlers_get._handle_events_sse`` used so a UI flipping
        between the legacy chain + the Router doesn't see header
        drift."""
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.send_header("X-Accel-Buffering", "no")
        handler.end_headers()

    def _drain(self, handler: Any, events_queue: Any) -> None:
        """Drain the queue forever. Either format + write each
        event matching ``self._topics``, or emit a heartbeat
        comment frame when the queue stays idle for the timeout
        window."""
        while True:
            try:
                ev = events_queue.get(timeout=self._heartbeat_seconds)
            except Empty:
                handler.wfile.write(events_sse_svc.HEARTBEAT_FRAME)
                handler.wfile.flush()
                continue
            if not events_sse_svc.event_matches_topics(ev, self._topics):
                continue
            handler.wfile.write(events_sse_svc.format_event_frame(ev))
            handler.wfile.flush()


class _SpecDumpStrategy:
    """Strategy choosing YAML vs JSON serialization for the OpenAPI
    spec dump. Both formats parse the same source YAML, graft in a
    runtime-built ``servers`` list, and fall back to a stub on parse
    error so a YAML break doesn't take ``/api/docs`` down entirely.
    Construction takes the YAML source string + the servers builder
    so the route methods stay pure dispatch."""

    def __init__(self, yaml_source: str, servers_builder: Any) -> None:
        self._yaml_source = yaml_source
        self._servers_builder = servers_builder

    def dump_json(self, handler: Any) -> None:
        """Emit the spec as JSON. On parse error, fall back to the
        legacy hardcoded stub via ``handler._get_openapi_spec``."""
        try:
            spec = self._parsed_with_servers()
            handler._json_response(HTTPStatus.OK, spec)
        except Exception as exc:  # noqa: BLE001
            log_swallowed(exc)
            handler._json_response(
                HTTPStatus.OK, handler._get_openapi_spec(),
            )

    def dump_yaml(self, handler: Any) -> None:
        """Re-emit the spec as YAML. On parse error, return the raw
        source unchanged. Content-type is ``text/yaml`` (NOT
        ``application/json``)."""
        try:
            spec = self._parsed_with_servers()
            rendered = _yaml.dump(
                spec, default_flow_style=False,
                sort_keys=False, allow_unicode=True,
            )
        except Exception:
            rendered = self._yaml_source
        handler._raw_response(
            HTTPStatus.OK,
            "text/yaml; charset=utf-8",
            rendered.encode("utf-8"),
        )

    def _parsed_with_servers(self) -> dict:
        """Parse the YAML once + graft the runtime servers list in.
        Shared between the JSON and YAML emitters so the spec each
        format sees stays identical."""
        spec = _yaml.safe_load(self._yaml_source) or {}
        spec["servers"] = self._servers_builder()
        return spec


class _QueryStringParser:
    """Parses the unstripped ``handler.path`` for the snapshot-diff
    handler. The dispatcher strips query strings before route
    matching, but ``handler.path`` retains them — so the parser
    reads off the unstripped form to recover the ``a`` + ``b``
    snapshot filenames."""

    def parse(self, raw_path: str) -> dict[str, str]:
        """Return the ``key=value`` pairs from the query string,
        or ``{}`` when no query string is present."""
        params: dict[str, str] = {}
        if "?" not in raw_path:
            return params
        query = raw_path.split("?", 1)[1]
        for part in query.split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
                params[key] = value
        return params


class MiscGetsGetRoutes(RouteModule):
    """Misc GET routes — events SSE + Grafana dashboard + OpenAPI
    spec dumps + snapshot-diff. The Router auto-discovers and
    instantiates this class at startup, then walks tagged methods
    for registration.

    Strategies are constructed once per instance (Router
    instantiates the module once at startup) so the openapi YAML
    source + servers builder are bound once rather than on every
    request."""

    def __init__(self) -> None:
        self._spec_dumper = _SpecDumpStrategy(
            _OPENAPI_YAML, _build_openapi_servers,
        )
        self._query_parser = _QueryStringParser()

    @get("/api/events")
    def handle_events_sse(self, handler: Any) -> None:
        """Stream typed domain events as Server-Sent Events.

        Parses the ``topics=`` query param, then hands off to the
        ``_SseEventEmitter`` strategy which owns the headers + the
        write loop. The dispatcher strips the query string before
        match but ``handler.path`` retains it, so the parser reads
        off the unstripped form.
        """
        params: dict[str, str] = {}
        if "?" in handler.path:
            qs = handler.path.split("?", 1)[1]
            parsed = parse_qs(qs, keep_blank_values=True)
            for key, values in parsed.items():
                if values:
                    params[key] = unquote(values[0])
        topics = events_sse_svc.parse_topics(params.get("topics"))
        emitter = _SseEventEmitter(topics)
        emitter.emit(handler)

    @get("/api/grafana.json")
    def handle_grafana_dashboard(self, handler: Any) -> None:
        """Pre-built Grafana dashboard JSON that queries the
        ``/metrics`` endpoint. Drop-in import for operators."""
        handler._json_response(
            HTTPStatus.OK, metrics_svc.get_grafana_dashboard(),
        )

    @get("/api/openapi.json")
    def handle_openapi_json(self, handler: Any) -> None:
        """Return the live ``contracts/api/openapi.yaml`` parsed
        to JSON, with the runtime-built ``servers`` list grafted
        in. Falls back to the legacy stub on parse error so
        ``/api/docs`` never goes dark."""
        self._spec_dumper.dump_json(handler)

    @get("/api/openapi.yaml")
    def handle_openapi_yaml(self, handler: Any) -> None:
        """Re-emit the OpenAPI spec as YAML (NOT JSON). Returns
        ``text/yaml; charset=utf-8`` so curl + ``swagger-cli`` see
        the right MIME type."""
        self._spec_dumper.dump_yaml(handler)

    @get("/api/snapshot-diff")
    def handle_snapshot_diff(self, handler: Any) -> None:
        """Diff between two config snapshots. Reads ``a`` + ``b``
        out of the unstripped ``handler.path`` query string."""
        params = self._query_parser.parse(handler.path)
        handler._json_response(
            HTTPStatus.OK,
            ops_svc.diff_snapshots(
                params.get("a", ""), params.get("b", ""),
            ),
        )


__all__ = ["MiscGetsGetRoutes"]
