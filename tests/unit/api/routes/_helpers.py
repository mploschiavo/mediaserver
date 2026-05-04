"""Shared test scaffolding for ``api/routes/*`` unit tests
(ADR-0007 Phase 1).

Each Phase 2 agent's ``test_<domain>.py`` imports
``MockControllerHandler`` and ``RouteDispatchHarness`` from this
module — mirroring the proof file ``test_health.py``. That keeps
every domain's tests written in the same shape, so reviewing one
test file teaches the pattern for all of them.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from media_stack.api.routing import (
    DefaultDispatcher,
    DispatchOutcome,
    Router,
    RouterDispatcher,
)


@dataclass
class CapturedResponse:
    """What a handler emitted via the
    ``ControllerAPIHandler``-shaped surface."""

    status: int = 0
    content_type: str = ""
    body: bytes = b""
    extra_headers: dict[str, str] = field(default_factory=dict)


class MockControllerHandler:
    """Stand-in for ``ControllerAPIHandler`` in route unit tests.

    Captures whatever a route handler writes via
    ``_json_response`` / ``_raw_response`` instead of going to the
    wire. The captured response is on ``self.captured``.

    Set ``state`` / ``path`` / ``headers`` etc. via constructor
    kwargs to mirror the production handler's surface.
    """

    def __init__(
        self,
        *,
        path: str = "/",
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> None:
        self.path = path
        self.headers = headers or {}
        self.state = state if state is not None else _MockState()
        self.rfile = io.BytesIO(body)
        self.captured = CapturedResponse()

    # --- ControllerAPIHandler surface --------------------------------

    def _json_response(self, status: int, body: Any) -> None:
        import json as _json
        self.captured.status = int(status)
        self.captured.content_type = "application/json"
        self.captured.body = _json.dumps(body).encode("utf-8")

    def _raw_response(
        self,
        status: int,
        content_type: str,
        body: bytes,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.captured.status = int(status)
        self.captured.content_type = content_type
        self.captured.body = body
        if extra_headers:
            self.captured.extra_headers.update(extra_headers)

    def _sse_response(self) -> None:
        # Cheap stub — most route tests don't exercise SSE; the
        # ones that do can subclass + override.
        self.captured.status = 200
        self.captured.content_type = "text/event-stream"


class _MockState:
    """Minimal stand-in for ``ControllerState`` used by handlers
    that read ``handler.state.<X>``. Tests can attach attributes
    on demand or pass a richer ``state=`` to the harness."""

    def __init__(self) -> None:
        self.initial_bootstrap_done = True
        self.phase = "ready"
        self.app_status: dict[str, Any] = {}
        self.runtime_config: dict[str, Any] = {}
        self.webhook_urls: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_bootstrap_done": self.initial_bootstrap_done,
            "phase": self.phase,
            "app_status": dict(self.app_status),
        }

    def get_failed_services(self) -> list[Any]:
        return []


class RouteDispatchHarness:
    """Test harness wrapping a ``RouterDispatcher`` instance.

    Tests do:

        harness = RouteDispatchHarness.with_default_router()
        response = harness.dispatch("GET", "/healthz")
        assert response.status == 200

    The harness uses the production ``DefaultDispatcher`` by
    default — same auto-discovery, same spec parity check. Tests
    that need an isolated dispatcher (e.g. for testing the Router
    against a fixture spec) construct ``RouteDispatchHarness(
    dispatcher=...)`` directly.
    """

    def __init__(self, dispatcher: RouterDispatcher) -> None:
        self._dispatcher = dispatcher

    @classmethod
    def with_default_router(cls) -> "RouteDispatchHarness":
        DefaultDispatcher.reset_for_tests()
        return cls(DefaultDispatcher.instance())

    @classmethod
    def with_custom_router(
        cls, router: Router,
    ) -> "RouteDispatchHarness":
        return cls(RouterDispatcher(router))

    def dispatch(
        self,
        verb: str,
        path: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> CapturedResponse:
        """Invoke the router with a ``MockControllerHandler``;
        return the captured response."""
        handler = MockControllerHandler(
            path=path, body=body, headers=headers, state=state,
        )
        outcome = self._dispatcher.try_dispatch(verb, path, handler)
        if outcome == DispatchOutcome.METHOD_NOT_ALLOWED:
            self._dispatcher.write_method_not_allowed(handler, path)
        return handler.captured

    def try_dispatch(
        self,
        verb: str,
        path: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        state: Any = None,
    ) -> tuple[DispatchOutcome, CapturedResponse]:
        """Like ``dispatch`` but also returns the outcome — useful
        for asserting NO_MATCH (fall-through during migration)."""
        handler = MockControllerHandler(
            path=path, body=body, headers=headers, state=state,
        )
        outcome = self._dispatcher.try_dispatch(verb, path, handler)
        return outcome, handler.captured


__all__ = [
    "CapturedResponse",
    "MockControllerHandler",
    "RouteDispatchHarness",
]
