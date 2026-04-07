"""Controller HTTP API server — thin routing layer over service modules.

Handles URL dispatch, auth, SSE streaming, and response formatting.
Business logic lives in api/services/*.py modules.
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

from .state import BootstrapState
from .services import health as health_svc
from .services import disk as disk_svc
from .services import content as content_svc
from .services import config as config_svc
from .services import admin as admin_svc
from .services import metrics as metrics_svc
from .services import ops as ops_svc

logger = logging.getLogger("controller_api")

ActionTriggerFn = Callable[[str, dict[str, Any]], None]


# ---------------------------------------------------------------------------
# TTL cache (shared across request handlers)
# ---------------------------------------------------------------------------

class _TTLCache:
    """Simple thread-safe TTL cache for expensive API responses."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry and (time.time() - entry[0]) < ttl:
                return entry[1]
        return None

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._store[key] = (time.time(), value)


_api_cache = _TTLCache()


# ---------------------------------------------------------------------------
# Webhook firing
# ---------------------------------------------------------------------------

def _fire_webhooks(state: BootstrapState, event: str, payload: dict[str, Any]) -> None:
    """Fire webhooks for action events (best-effort, non-blocking)."""
    urls = list(state.webhook_urls)
    if not urls:
        return
    data = json.dumps({"event": event, **payload}).encode("utf-8")
    for url in urls:
        try:
            req = urllib.request.Request(
                url, data=data, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

_DASHBOARD_HTML_PATH = Path(__file__).parent / "dashboard.html"
_DASHBOARD_HTML = ""
try:
    _DASHBOARD_HTML = _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
except Exception:
    _DASHBOARD_HTML = "<html><body><h1>Dashboard not found</h1></body></html>"


# ---------------------------------------------------------------------------
# Auth + known actions
# ---------------------------------------------------------------------------

_AUTH_REQUIRED_PATHS = frozenset({
    "/api/rotate-keys", "/api/reset-password", "/api/routing",
    "/api/batch-restart", "/api/profile", "/api/envvars",
    "/api/guardrails", "/webhooks/test", "/config",
})
_AUTH_REQUIRED_PREFIXES = ("/actions/", "/api/restart/")

KNOWN_ACTIONS = frozenset({
    "bootstrap", "finalize", "auto-indexers", "restart-apps",
    "sync-indexers", "envoy-config", "reconcile",
})


# ---------------------------------------------------------------------------
# Request Handler
# ---------------------------------------------------------------------------

class ControllerAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for controller API endpoints."""

    state: BootstrapState
    action_trigger: ActionTriggerFn | None = None
    reload_config: Callable[[], None] | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        logger.debug("[%s] %s %s", ts, self.command, self.path)

    # --- Auth ---

    def _check_auth(self) -> bool:
        """Check Basic Auth for sensitive endpoints. GET is public, POST/PUT/DELETE require auth."""
        path = self.path.split("?")[0]
        if self.command == "GET":
            return True  # All GET endpoints are read-only and public
        needs_auth = self.command in ("POST", "PUT", "DELETE")
        if not needs_auth:
            return True
        username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        password = os.environ.get("STACK_ADMIN_PASSWORD", "")
        if not password:
            return True
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
        if self.action_trigger:
            self.action_trigger(action_name, overrides)
        self._json_response(200, {"status": "accepted", "action": action_name, "overrides": overrides})

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
            ("/api/health", "Live service health probes"),
            ("/api/versions", "Service versions"),
            ("/api/downloads", "Active downloads"),
            ("/api/stats", "Library counts"),
            ("/api/indexers", "Prowlarr indexers"),
            ("/api/disk", "Disk usage + guardrails"),
            ("/api/env", "Runtime environment"),
            ("/api/routing", "Routing configuration"),
            ("/api/profile", "Bootstrap profile"),
            ("/api/namespaces", "Containers / namespaces"),
            ("/api/libraries", "Jellyfin libraries"),
            ("/api/image-updates", "Image versions + staleness"),
            ("/api/gpu", "GPU detection for transcoding"),
            ("/api/snapshots", "Config snapshots"),
            ("/api/mounts", "Filesystem mounts"),
            ("/api/backup", "Config backup download"),
            ("/metrics", "Prometheus metrics"),
            ("/api/feed.xml", "RSS feed"),
            ("/api/openapi.json", "This spec"),
        ]
        post_endpoints = [
            ("/actions/{name}", "Trigger action"),
            ("/api/rotate-keys", "Rotate all API keys"),
            ("/api/reset-password", "Reset admin password"),
            ("/api/routing", "Update routing config"),
            ("/api/guardrails", "Update guardrail settings"),
            ("/api/batch-restart", "Restart multiple services"),
            ("/config", "Update runtime config"),
        ]
        paths: dict[str, Any] = {}
        for ep, desc in get_endpoints:
            paths[ep] = {"get": {"summary": desc, "responses": {"200": {"description": "OK"}}}}
        for ep, desc in post_endpoints:
            paths[ep] = {"post": {"summary": desc, "responses": {"200": {"description": "OK"}}}}
        return {
            "openapi": "3.0.3",
            "info": {"title": "Media Stack Controller API", "version": "1.0.0"},
            "paths": paths,
        }

    # =======================================================================
    # GET routing
    # =======================================================================

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        path = self.path.split("?")[0]

        # --- Probes ---
        if path == "/healthz":
            self._json_response(200, {"status": "ok"})
        elif path == "/readyz":
            self._json_response(200, {
                "status": "ready",
                "initial_bootstrap_done": self.state.initial_bootstrap_done,
                "phase": self.state.phase,
            })

        # --- State ---
        elif path == "/status":
            self._json_response(200, self.state.to_dict())
        elif path == "/apps":
            self._json_response(200, {"apps": dict(self.state.app_status)})
        elif path.startswith("/apps/") and path.count("/") == 2:
            app_name = path.split("/")[2]
            info = self.state.app_status.get(app_name)
            self._json_response(200 if info else 404, {app_name: info} if info else {"error": f"app '{app_name}' not found"})
        elif path == "/config":
            self._json_response(200, {"config": dict(self.state.runtime_config)})
        elif path == "/webhooks":
            self._json_response(200, {"webhook_urls": list(self.state.webhook_urls)})

        # --- SSE ---
        elif path == "/logs/stream":
            self._sse_response()

        # --- Health ---
        elif path == "/api/health":
            result = health_svc.probe_services(_api_cache)
            health_svc.append_health_history(result.get("services", {}))
            self._json_response(200, result)
        elif path == "/api/health-history":
            self._json_response(200, health_svc.get_health_history())

        # --- Content ---
        elif path == "/api/versions":
            self._json_response(200, content_svc.get_versions(_api_cache))
        elif path == "/api/downloads":
            self._json_response(200, content_svc.get_downloads())
        elif path == "/api/stats":
            self._json_response(200, content_svc.get_stats(_api_cache))
        elif path == "/api/indexers":
            self._json_response(200, content_svc.get_indexers())
        elif path == "/api/indexer-stats":
            self._json_response(200, content_svc.get_indexer_stats())
        elif path == "/api/download-history":
            self._json_response(200, content_svc.get_download_history())
        elif path == "/api/quality-profiles":
            self._json_response(200, content_svc.get_quality_profiles())
        elif path == "/api/import-lists":
            self._json_response(200, content_svc.get_import_lists())
        elif path == "/api/libraries":
            self._json_response(200, content_svc.get_jellyfin_libraries())
        elif path == "/api/recent":
            self._json_response(200, content_svc.get_recent())

        # --- Disk ---
        elif path == "/api/disk":
            self._json_response(200, disk_svc.get_disk())

        # --- Config ---
        elif path == "/api/env":
            self._json_response(200, config_svc.get_env())
        elif path == "/api/routing":
            self._json_response(200, config_svc.get_routing())
        elif path == "/api/profile":
            self._json_response(200, config_svc.get_profile())
        elif path == "/api/manifests":
            self._json_response(200, config_svc.get_manifests())
        elif path == "/api/envvars":
            self._json_response(200, config_svc.get_envvars())
        elif path == "/api/backup":
            payload = config_svc.get_backup(self.state)
            self._raw_response(200, "application/json", payload, {
                "Content-Disposition": f'attachment; filename="media-stack-backup-{time.strftime("%Y%m%d-%H%M%S")}.json"',
            })

        # --- Ops ---
        elif path == "/api/namespaces":
            self._json_response(200, ops_svc.get_namespaces())
        elif path == "/api/image-updates":
            self._json_response(200, ops_svc.check_image_updates())
        elif path == "/api/gpu":
            self._json_response(200, ops_svc.get_gpu_info())
        elif path == "/api/snapshots":
            self._json_response(200, ops_svc.get_config_snapshots())
        elif path == "/api/mounts":
            self._json_response(200, ops_svc.get_mount_info())
        elif path.startswith("/api/logs/") and path.count("/") == 3:
            svc = path.split("/")[3]
            lines = 100
            if "?" in self.path:
                for part in self.path.split("?", 1)[1].split("&"):
                    if part.startswith("lines="):
                        try:
                            lines = min(500, int(part.split("=", 1)[1]))
                        except ValueError:
                            pass
            self._json_response(200, ops_svc.get_service_logs(svc, lines))

        # --- Metrics ---
        elif path == "/metrics":
            self._raw_response(200, "text/plain; version=0.0.4; charset=utf-8",
                               metrics_svc.get_prometheus_metrics(_api_cache).encode("utf-8"))
        elif path == "/api/envoy/stats":
            self._json_response(200, metrics_svc.get_envoy_stats())
        elif path == "/api/feed.xml":
            self._raw_response(200, "application/rss+xml; charset=utf-8",
                               metrics_svc.get_rss_feed(self.state, _api_cache).encode("utf-8"))
        elif path == "/api/grafana.json":
            self._json_response(200, metrics_svc.get_grafana_dashboard())
        elif path == "/api/openapi.json":
            self._json_response(200, self._get_openapi_spec())

        # --- Dashboard ---
        elif path in ("/", "/dashboard"):
            html = _DASHBOARD_HTML
            plugins = self._load_plugins()
            if plugins:
                html = html.replace("</body>", plugins + "\n</body>")
            self._html_response(200, html)
        elif path == "/api/docs":
            self._html_response(200, '<html><head><meta http-equiv="refresh" content="0;url=/api/openapi.json"></head></html>')

        else:
            self._json_response(404, {"error": "not found"})

    # =======================================================================
    # POST routing
    # =======================================================================

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return

        # POST /run — backward-compatible alias
        if self.path == "/run":
            self._handle_action("bootstrap")
            return

        # POST /api/restart/{service}
        if self.path.startswith("/api/restart/"):
            svc = self.path[len("/api/restart/"):]
            self._json_response(200, admin_svc.restart_service(svc))
            return

        # POST /api/batch-restart
        if self.path == "/api/batch-restart":
            body = self._read_json_body()
            services = body.get("services", [])
            if not services:
                self._json_response(400, {"error": "services list required"})
                return
            self._json_response(200, admin_svc.batch_restart(services))
            return

        # POST /api/rotate-keys
        if self.path == "/api/rotate-keys":
            self._json_response(200, admin_svc.rotate_keys())
            return

        # POST /api/reset-password
        if self.path == "/api/reset-password":
            body = self._read_json_body()
            new_password = body.get("password", "")
            if not new_password or len(new_password) < 4:
                self._json_response(400, {"error": "password field required (min 4 chars)"})
                return
            self._json_response(200, admin_svc.reset_password(new_password))
            return

        # POST /api/routing
        if self.path == "/api/routing":
            body = self._read_json_body()
            if not body:
                self._json_response(400, {"error": "JSON body required"})
                return
            self._json_response(200, config_svc.update_routing(body, self.action_trigger))
            return

        # POST /api/guardrails
        if self.path == "/api/guardrails":
            body = self._read_json_body()
            if not body:
                self._json_response(400, {"error": "JSON body required"})
                return
            self._json_response(200, disk_svc.update_guardrails(body))
            return

        # POST /api/profile
        if self.path == "/api/profile":
            body = self._read_json_body()
            content = body.get("content", "")
            if not content:
                self._json_response(400, {"error": "content field required"})
                return
            self._json_response(200, config_svc.save_profile(content, self.reload_config))
            return

        # POST /api/envvars
        if self.path == "/api/envvars":
            body = self._read_json_body()
            key = body.get("key", "")
            value = body.get("value", "")
            if not key:
                self._json_response(400, {"error": "key field required"})
                return
            self._json_response(200, config_svc.set_envvar(key, value))
            return

        # POST /webhooks/test
        if self.path == "/webhooks/test":
            self._json_response(200, self._test_webhook())
            return

        # POST /actions/{name}
        if self.path.startswith("/actions/"):
            action_name = self.path[len("/actions/"):]
            if action_name not in KNOWN_ACTIONS:
                self._json_response(404, {"error": f"unknown action '{action_name}'", "known": sorted(KNOWN_ACTIONS)})
                return
            self._handle_action(action_name)
            return

        # POST /config
        if self.path == "/config":
            body = self._read_json_body()
            if not body:
                self._json_response(400, {"error": "JSON body required"})
                return
            updated = self.state.update_config(body)
            logger.info("Config updated: %s", body)
            self._json_response(200, {"status": "updated", "config": updated})
            return

        # POST /webhooks
        if self.path == "/webhooks":
            body = self._read_json_body()
            url = body.get("url", "").strip()
            if url:
                self.state.webhook_urls.add(url)
            self._json_response(200, {"webhook_urls": list(self.state.webhook_urls)})
            return

        self._json_response(404, {"error": "not found"})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

def start_api_server(
    state: BootstrapState,
    port: int = 9100,
    action_trigger: ActionTriggerFn | None = None,
    reload_config: Callable[[], None] | None = None,
) -> ThreadingHTTPServer:
    """Start the API server in a background thread."""
    ControllerAPIHandler.state = state
    ControllerAPIHandler.action_trigger = action_trigger
    ControllerAPIHandler.reload_config = reload_config

    server = ThreadingHTTPServer(("0.0.0.0", port), ControllerAPIHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="api-server")
    thread.start()

    # Graceful shutdown on SIGTERM
    def _shutdown(signum: int, frame: Any) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    return server
