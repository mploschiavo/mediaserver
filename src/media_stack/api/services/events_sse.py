"""SSE filter + frame-formatting helpers for ``GET /api/events``.

The events endpoint forwards typed ``Event`` instances published on
the process-wide ``EventBus`` to subscribed UI clients. The filter
shape is a comma-separated ``topics=`` query param; each event's
``event_type`` (e.g. ``"job.started"``) is reduced to a *topic*
(``"jobs"``) and a frame is only emitted if the request asked for
that topic. Empty / missing ``topics`` means "all known topics" so
naive curl probes see traffic without having to enumerate.

This module is deliberately pure — no I/O, no bus reference. Tests
exercise the helpers directly without spinning up an HTTP server.
The actual SSE handler at ``handlers_get._handle_events_sse`` wires
these helpers to a per-request ``queue.Queue`` subscribed to the
bus.

Topic mapping is a single dictionary so adding a new domain (e.g.
``access_log``, ``health``, ``guardrails``) is a single line; the
mapping is intentionally narrow rather than a regex so an unknown
event-type prefix surfaces as ``""`` and the forwarder drops it
silently rather than leaking an unmapped class onto the wire.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from media_stack.core.events.bus import Event

# Map event-type prefix (the dotted segment before the first ``.``) to
# the operator-facing topic. The set of values defines the public
# ``topics=`` enum exposed in OpenAPI.
EVENT_TYPE_TO_TOPIC: Mapping[str, str] = {
    "job": "jobs",
    "login": "sessions",
    "session": "sessions",
    "ban": "sessions",
    "password": "sessions",
    "security": "sessions",
    "media_integrity": "media_integrity",
    "storage": "storage",
}

# Topics callers may request. Used to validate the ``topics=`` query
# param and to seed the OpenAPI enum. Computed from the mapping so the
# two stay in sync without manual maintenance.
KNOWN_TOPICS: frozenset[str] = frozenset(EVENT_TYPE_TO_TOPIC.values())


def event_topic(event_type: str) -> str:
    """Reduce a dotted ``event_type`` to its operator-facing topic.

    ``"job.started"`` → ``"jobs"``; ``"login.failed"`` → ``"sessions"``;
    an unknown prefix returns ``""`` so the forwarder can filter the
    event out rather than guess at its routing.
    """
    if not event_type:
        return ""
    prefix = event_type.split(".", 1)[0]
    return EVENT_TYPE_TO_TOPIC.get(prefix, "")


def parse_topics(raw: str | None) -> frozenset[str]:
    """Parse the ``topics=`` query param into a topic set.

    Empty / missing → all known topics. Unknown topic names are
    silently dropped (rather than raising) so a UI shipping a future
    topic name keeps working against an older controller — the older
    controller just doesn't deliver that traffic.
    """
    if not raw:
        return KNOWN_TOPICS
    requested = {t.strip() for t in raw.split(",") if t.strip()}
    out = requested & set(KNOWN_TOPICS)
    return frozenset(out) if out else frozenset()


def event_matches_topics(
    event: Event, topics: frozenset[str]
) -> bool:
    """True iff ``event`` should be forwarded to a client filtering on
    ``topics``. Events whose type doesn't map to any known topic are
    always dropped — they're not part of the public contract."""
    topic = event_topic(event.event_type)
    return bool(topic) and topic in topics


def format_event_frame(event: Event) -> bytes:
    """Encode an ``Event`` as an SSE block.

    Frame shape:

        event: <topic>.<sub>
        data: <json-payload>

    The ``event:`` line carries the full dotted ``event_type`` so a
    UI that subscribed via ``addEventListener("job.started", …)``
    receives only that subtype without having to JSON-parse to find
    out. The ``data:`` line is a JSON object containing every
    dataclass field — including ``event_type`` and ``ts`` — so a
    consumer that only reads the default ``message`` event still has
    the full record.
    """
    payload: dict[str, Any] = event.to_dict()
    blob = json.dumps(payload, separators=(",", ":"), default=str)
    return f"event: {event.event_type}\ndata: {blob}\n\n".encode()


# Heartbeat frame the SSE handler emits when the bus has been quiet
# for a while. Comments (``: ...``) are valid SSE per the spec and
# never trigger a client-side ``message`` event — they exist purely
# to keep proxies (Envoy, nginx) from idle-killing the connection.
HEARTBEAT_FRAME: bytes = b": heartbeat\n\n"
