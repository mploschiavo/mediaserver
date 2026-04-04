"""Lightweight HTTP API server for bootstrap runner telemetry and control."""

from __future__ import annotations

import json
import signal
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .state import BootstrapState

RunTriggerFn = Callable[[], None]


class BootstrapAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for bootstrap lifecycle and preflight endpoints."""

    state: BootstrapState
    run_trigger: RunTriggerFn | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Suppress default stderr logging; callers can wire a real logger.
        pass

    def _json_response(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json_response(200, {"status": "ok"})
        elif self.path == "/readyz":
            if self.state.is_complete and self.state.error is None:
                self._json_response(200, {"status": "ready"})
            else:
                self._json_response(503, {"status": self.state.phase})
        elif self.path == "/status":
            self._json_response(200, self.state.to_dict())
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/run":
            if self.state.is_running:
                self._json_response(409, {"error": "bootstrap already running"})
            elif self.state.is_complete:
                self._json_response(409, {"error": "bootstrap already completed"})
            elif self.run_trigger is not None:
                self.run_trigger()
                self._json_response(202, {"status": "accepted"})
            else:
                self._json_response(503, {"error": "no run trigger configured"})
        else:
            self._json_response(404, {"error": "not found"})


def start_api_server(
    state: BootstrapState,
    *,
    port: int = 9100,
    run_trigger: RunTriggerFn | None = None,
) -> ThreadingHTTPServer:
    """Start the bootstrap API HTTP server on a daemon thread.

    Returns the server instance so callers can shut it down.
    """

    handler_class = type(
        "BoundHandler",
        (BootstrapAPIHandler,),
        {"state": state, "run_trigger": run_trigger},
    )

    server = ThreadingHTTPServer(("0.0.0.0", port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Register graceful shutdown on SIGTERM (Docker/K8s sends this on stop).
    original_handler = signal.getsignal(signal.SIGTERM)

    def _shutdown(signum: int, frame: Any) -> None:
        server.shutdown()
        if callable(original_handler) and original_handler not in (
            signal.SIG_DFL,
            signal.SIG_IGN,
        ):
            original_handler(signum, frame)

    signal.signal(signal.SIGTERM, _shutdown)

    return server
