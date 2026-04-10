"""Controller HTTP API server — thin routing layer over service modules.

Handles URL dispatch, auth, SSE streaming, and response formatting.
Business logic lives in api/services/*.py modules.
Route handling lives in api/handlers_get.py and api/handlers_post.py.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import signal
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .state import ControllerState
from . import handlers_get
from . import handlers_post

# Re-export for backward compatibility — other modules import these from server.py
from .webhooks import _fire_webhooks  # noqa: F401
from .cache import api_cache as _api_cache  # noqa: F401
from .handlers_get import _build_openapi_servers  # noqa: F401

logger = logging.getLogger("controller_api")

ActionTriggerFn = Callable[[str, dict[str, Any]], None]


# Re-export constants that other modules import from server.py
KNOWN_ACTIONS = handlers_post.KNOWN_ACTIONS

# Lower number = higher priority. Used by PriorityQueue in the dispatch loop.
ACTION_PRIORITY: dict[str, int] = {
    "bootstrap":     10,
    "finalize":      20,
    "envoy-config":  30,
    "restart-apps":  40,
    "reconcile":     50,
    "sync-indexers": 60,
    "auto-indexers": 70,
    "validate-credentials": 80,
}
DEFAULT_ACTION_PRIORITY = 50


# ---------------------------------------------------------------------------
# Auth + known actions
# ---------------------------------------------------------------------------

_AUTH_REQUIRED_PATHS = frozenset({
    "/api/rotate-keys", "/api/reset-password", "/api/routing",
    "/api/batch-restart", "/api/profile", "/api/envvars",
    "/api/guardrails", "/webhooks/test", "/config", "/cancel",
})
_AUTH_REQUIRED_PREFIXES = ("/actions/", "/api/restart/")


# ---------------------------------------------------------------------------
# Request Handler
# ---------------------------------------------------------------------------

class ControllerAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for controller API endpoints."""

    state: ControllerState
    _callbacks: dict[str, Any] = {}

    @property
    def action_trigger(self) -> ActionTriggerFn | None:
        return self._callbacks.get("action_trigger")

    @property
    def reload_config(self) -> Callable[[], None] | None:
        return self._callbacks.get("reload_config")

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        logger.debug("[%s] %s %s", ts, self.command, self.path)

    # --- Auth ---

    def _check_auth(self) -> bool:
        """Check Basic Auth.

        CONTROLLER_AUTH modes:
          "all"   — protect all endpoints (dashboard + API + write)
          "write" — protect POST/PUT/DELETE only (default when password is set)
          "none"  — no auth (default when no password)

        /healthz and /readyz are always public (K8s probes).
        """
        path = self.path.split("?")[0]

        # Probes are always public
        if path in ("/healthz", "/readyz"):
            return True

        username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        password = os.environ.get("STACK_ADMIN_PASSWORD", "")
        auth_mode = os.environ.get("CONTROLLER_AUTH", "").strip().lower()

        # Determine effective mode
        if not auth_mode:
            auth_mode = "write" if password else "none"

        if auth_mode == "none":
            return True
        if auth_mode == "write" and self.command == "GET":
            return True

        # Auth required — check credentials
        if not password:
            return True  # No password configured, can't authenticate
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                provided_user, _, provided_pass = decoded.partition(":")
                if provided_user == username and provided_pass == password:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Media Stack Controller"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    # --- Response helpers ---

    def _json_response(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _html_response(self, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _raw_response(self, status: int, content_type: str, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return {}

    # --- SSE ---

    def _sse_response(self) -> None:
        """Send Server-Sent Events stream of log lines."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        after_seq = 0
        if "?" in self.path:
            qs = self.path.split("?", 1)[1]
            for part in qs.split("&"):
                if part.startswith("after_seq="):
                    try:
                        after_seq = int(part.split("=", 1)[1])
                    except ValueError:
                        pass

        try:
            while True:
                entries = self.state.get_logs_since(after_seq)
                for seq, ts, msg, action, *_ in entries:
                    ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
                    data = json.dumps({"seq": seq, "ts": ts_str, "msg": msg, "action": action})
                    self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode())
                    after_seq = seq
                self.wfile.flush()
                self.state.wait_for_log(timeout=30.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    # --- Action dispatch ---

    def _handle_action(self, action_name: str) -> None:
        body = self._read_json_body()
        overrides = body if body else {}
        # Capture who triggered this action
        auth_header = self.headers.get("Authorization", "")
        triggered_by = "system"
        if auth_header.startswith("Basic "):
            try:
                import base64
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                triggered_by = decoded.partition(":")[0] or "user"
            except Exception:
                triggered_by = "user"
        overrides["_triggered_by"] = triggered_by
        if self.action_trigger:
            self.action_trigger(action_name, overrides)
        priority = ACTION_PRIORITY.get(action_name, DEFAULT_ACTION_PRIORITY)
        self._json_response(200, {
            "status": "accepted",
            "action": action_name,
            "priority": priority,
            "overrides": overrides,
        })

    # --- Plugin loader ---

    def _load_plugins(self) -> str:
        """Load custom JS/CSS from config mount."""
        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        plugin_dir = Path(config_root) / "controller" / "plugins"
        if not plugin_dir.is_dir():
            return ""
        parts: list[str] = []
        for f in sorted(plugin_dir.iterdir()):
            if f.suffix == ".js" and f.is_file():
                parts.append(f"<script>{f.read_text(encoding='utf-8')}</script>")
            elif f.suffix == ".css" and f.is_file():
                parts.append(f"<style>{f.read_text(encoding='utf-8')}</style>")
        return "\n".join(parts)

    # --- Webhook test ---

    def _test_webhook(self) -> dict[str, Any]:
        urls = list(self.state.webhook_urls)
        if not urls:
            return {"status": "no_webhooks", "tested": 0}
        results: dict[str, str] = {}
        data = json.dumps({"event": "test", "status": "ok"}).encode("utf-8")
        for url in urls:
            try:
                req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    results[url] = f"ok ({resp.status})"
            except Exception as exc:
                results[url] = f"error: {str(exc)[:60]}"
        return {"status": "tested", "results": results, "tested": len(results)}

    # --- OpenAPI spec ---

    def _get_openapi_spec(self) -> dict[str, Any]:
        get_endpoints = [
            ("/healthz", "Liveness probe"),
            ("/readyz", "Readiness probe"),
            ("/status", "Full controller state"),
            ("/apps", "All app statuses"),
            ("/apps/{name}", "Single app status"),
            ("/config", "Runtime configuration"),
            ("/webhooks", "List webhook URLs"),
            ("/logs/stream", "Real-time log stream (SSE)"),
            ("/api/services", "List managed services"),
            ("/api/services/categories", "Service categories"),
            ("/api/services/{id}/api-key", "API key status for a service"),
            ("/api/health", "Live service health probes"),
            ("/api/health-history", "Health history and SLA metrics"),
            ("/api/versions", "Service versions"),
            ("/api/downloads", "Active downloads"),
            ("/api/stats", "Library counts"),
            ("/api/indexers", "Indexer manager entries"),
            ("/api/indexer-stats", "Indexer performance stats"),
            ("/api/download-history", "Recent download history"),
            ("/api/quality-profiles", "Quality profiles"),
            ("/api/import-lists", "Import/discovery lists"),
            ("/api/libraries", "Media server libraries"),
            ("/api/recent", "Recently added items"),
            ("/api/keys", "All API keys and admin credentials"),
            ("/api/disk", "Disk usage + guardrails"),
            ("/api/cleanup-preview", "Guardrail cleanup preview"),
            ("/api/env", "Runtime environment"),
            ("/api/routing", "Routing configuration"),
            ("/api/profile", "Bootstrap profile"),
            ("/api/envvars", "Stack environment variables"),
            ("/api/manifests", "Deployment manifests"),
            ("/api/backup", "Config backup download"),
            ("/api/namespaces", "Containers / namespaces"),
            ("/api/image-updates", "Image versions + staleness"),
            ("/api/gpu", "GPU detection for transcoding"),
            ("/api/snapshots", "Config snapshots"),
            ("/api/snapshots/{file}", "Snapshot detail"),
            ("/api/snapshot-diff", "Compare two snapshots"),
            ("/api/mounts", "Filesystem mounts"),
            ("/api/logs/{service}", "Service container logs"),
            ("/metrics", "Prometheus metrics"),
            ("/api/envoy/stats", "Envoy proxy traffic stats"),
            ("/api/feed.xml", "RSS feed"),
            ("/api/grafana.json", "Grafana dashboard JSON"),
            ("/api/openapi.json", "This spec (abridged)"),
            ("/api/openapi.yaml", "Full OpenAPI 3.0.3 spec"),
        ]
        post_endpoints = [
            ("/actions/{name}", "Trigger action"),
            ("/cancel", "Cancel running action"),
            ("/run", "Trigger bootstrap (legacy)"),
            ("/api/services/{id}/api-key", "Set/discover API key"),
            ("/api/rotate-keys", "Rotate all API keys"),
            ("/api/reset-password", "Reset admin password"),
            ("/api/media-server/reset", "Hard-reset media server credentials"),
            ("/api/routing", "Update routing config"),
            ("/api/guardrails", "Update guardrail settings"),
            ("/api/profile", "Save bootstrap profile"),
            ("/api/envvars", "Set environment variable"),
            ("/api/restore", "Restore config from backup"),
            ("/api/batch-restart", "Restart multiple services"),
            ("/api/restart/{service}", "Restart a single service"),
            ("/api/gpu/enable", "Auto-configure GPU transcoding"),
            ("/api/snapshot", "Take a config snapshot"),
            ("/config", "Update runtime config"),
            ("/webhooks", "Register webhook URL"),
            ("/webhooks/test", "Test all webhooks"),
        ]
        paths: dict[str, Any] = {}
        for ep, desc in get_endpoints:
            paths[ep] = {"get": {"summary": desc, "responses": {"200": {"description": "OK"}}}}
        for ep, desc in post_endpoints:
            paths[ep] = {"post": {"summary": desc, "responses": {"200": {"description": "OK"}}}}
        return {
            "openapi": "3.0.3",
            "info": {
                "title": "Media Stack Controller API",
                "version": "1.0.0",
                "description": "Abridged spec. For the full OpenAPI 3.0.3 specification with schemas, examples, and descriptions, see GET /api/openapi.yaml or visit /api/docs.",
            },
            "paths": paths,
        }

    # =======================================================================
    # GET routing — delegates to handlers_get
    # =======================================================================

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        handlers_get.handle(self)

    # =======================================================================
    # POST routing — delegates to handlers_post
    # =======================================================================

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        handlers_post.handle(self)


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_api_server(
    state: ControllerState,
    port: int = 9100,
    action_trigger: ActionTriggerFn | None = None,
    reload_config: Callable[[], None] | None = None,
) -> ThreadingHTTPServer:
    """Start the API server in a background thread."""
    ControllerAPIHandler.state = state
    # Store callables in a dict to avoid Python's descriptor protocol
    # binding them to self when accessed as class attributes.
    ControllerAPIHandler._callbacks = {
        "action_trigger": action_trigger,
        "reload_config": reload_config,
    }

    server = ThreadingHTTPServer(("0.0.0.0", port), ControllerAPIHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="api-server")
    thread.start()

    # Graceful shutdown on SIGTERM
    def _shutdown(signum: int, frame: Any) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    return server
