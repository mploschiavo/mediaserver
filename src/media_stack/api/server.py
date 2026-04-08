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

from .state import ControllerState
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

def _fire_webhooks(state: ControllerState, event: str, payload: dict[str, Any]) -> None:
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
    "/api/guardrails", "/webhooks/test", "/config", "/cancel",
})
_AUTH_REQUIRED_PREFIXES = ("/actions/", "/api/restart/")

KNOWN_ACTIONS = frozenset({
    "bootstrap", "finalize", "auto-indexers", "restart-apps",
    "sync-indexers", "envoy-config", "reconcile",
})

# Lower number = higher priority. Used by PriorityQueue in the dispatch loop.
ACTION_PRIORITY: dict[str, int] = {
    "bootstrap":     10,
    "finalize":      20,
    "envoy-config":  30,
    "restart-apps":  40,
    "reconcile":     50,
    "sync-indexers": 60,
    "auto-indexers": 70,
}
DEFAULT_ACTION_PRIORITY = 50


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

        # --- Services (registry) ---
        elif path == "/api/services":
            from media_stack.api.services.registry import SERVICES
            svc_list = [
                {"id": s.id, "name": s.name, "desc": s.desc, "category": s.category,
                 "host": s.host, "port": s.port}
                for s in SERVICES
            ]
            # Include the controller itself as a virtual service entry
            ctrl_port = int(os.environ.get("CONTROLLER_PORT", "9876"))
            svc_list.append({
                "id": "controller", "name": "Media Stack Controller",
                "desc": "Orchestration API and dashboard",
                "category": "infrastructure", "host": "localhost", "port": ctrl_port,
            })
            self._json_response(200, svc_list)
        elif path == "/api/services/categories":
            from media_stack.api.services.registry import CATEGORIES
            import copy
            cats = copy.deepcopy(CATEGORIES)
            # Ensure controller appears in infrastructure category
            infra = next((c for c in cats if c["label"].lower() == "infrastructure"), None)
            if infra:
                if "controller" not in infra["ids"]:
                    infra["ids"].append("controller")
            else:
                cats.append({"label": "Infrastructure", "ids": ["controller"]})
            self._json_response(200, cats)

        # --- Per-service API key status ---
        elif path.startswith("/api/services/") and path.endswith("/api-key"):
            parts = path.split("/")
            svc_id = parts[3] if len(parts) >= 5 else ""
            from media_stack.api.services.registry import SERVICE_MAP
            svc = SERVICE_MAP.get(svc_id)
            if not svc or not svc.api_key_env:
                self._json_response(404, {"error": f"Service '{svc_id}' not found or has no API key"})
            else:
                current = (os.environ.get(svc.api_key_env) or "").strip()
                self._json_response(200, {
                    "service": svc_id, "env": svc.api_key_env,
                    "has_key": bool(current),
                    "key_preview": f"{current[:4]}...{current[-4:]}" if len(current) > 8 else ("set" if current else ""),
                })

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
        elif path == "/api/cleanup-preview":
            self._json_response(200, disk_svc.preview_cleanup())

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
        elif path.startswith("/api/snapshots/") and path.count("/") == 3:
            filename = path.split("/")[3]
            self._json_response(200, ops_svc.get_snapshot_detail(filename))
        elif path == "/api/snapshot-diff":
            # ?a=snapshot-xxx.json&b=snapshot-yyy.json
            params = {}
            if "?" in self.path:
                for part in self.path.split("?", 1)[1].split("&"):
                    if "=" in part:
                        k, v = part.split("=", 1)
                        params[k] = v
            self._json_response(200, ops_svc.diff_snapshots(params.get("a", ""), params.get("b", "")))
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
            spec = self._get_openapi_spec()
            rows = ""
            for ep, info in sorted(spec.get("paths", {}).items()):
                for method, details in info.items():
                    color = "#4ade80" if method == "get" else "#3b82f6"
                    rows += f'<tr><td><span style="color:{color};font-weight:700;text-transform:uppercase;font-size:.82em">{method}</span></td><td style="font-family:monospace">{ep}</td><td>{details.get("summary","")}</td></tr>'
            html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>API Documentation</title>
<style>body{{font-family:system-ui;background:#0f1923;color:#e0e0e0;margin:0;padding:24px}}
h1{{color:#4ade80;font-size:1.4em}}a{{color:#3b82f6}}
table{{width:100%;border-collapse:collapse;margin-top:16px}}
th{{text-align:left;padding:8px;border-bottom:2px solid #1e3044;color:#94a3b8;font-size:.82em;text-transform:uppercase}}
td{{padding:6px 8px;border-bottom:1px solid #1e3044;font-size:.9em}}
tr:hover{{background:#162230}}</style></head><body>
<h1>Media Stack Controller API</h1>
<p style="color:#94a3b8">Base URL: <code>http://localhost:9100</code> · <a href="/api/openapi.json">OpenAPI JSON</a> · <a href="/">Dashboard</a></p>
<table><thead><tr><th>Method</th><th>Endpoint</th><th>Description</th></tr></thead><tbody>{rows}</tbody></table>
<p style="color:#64748b;margin-top:24px;font-size:.82em">POST endpoints require Basic Auth (admin credentials). GET endpoints are public.</p>
</body></html>"""
            self._html_response(200, html)

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
            body = self._read_json_body() or {}
            target = body.get("services")  # optional list of service IDs
            self._json_response(200, admin_svc.rotate_keys(target))
            return

        # POST /api/reset-password
        if self.path == "/api/reset-password":
            body = self._read_json_body()
            new_password = body.get("password", "")
            if not new_password or len(new_password) < 4:
                self._json_response(400, {"error": "password field required (min 4 chars)"})
                return
            target = body.get("services")  # optional list of service IDs
            self._json_response(200, admin_svc.reset_password(new_password, target))
            return

        # POST /api/services/{id}/api-key — manually set or discover a service API key
        if self.path.startswith("/api/services/") and self.path.endswith("/api-key"):
            parts = self.path.split("/")
            svc_id = parts[3] if len(parts) >= 5 else ""
            from media_stack.api.services.registry import SERVICE_MAP, read_api_key_from_file, read_api_key_via_http
            svc = SERVICE_MAP.get(svc_id)
            if not svc or not svc.api_key_env:
                self._json_response(404, {"error": f"Service '{svc_id}' not found or has no API key"})
                return
            body = self._read_json_body() or {}
            manual_key = str(body.get("api_key", "")).strip()
            if manual_key:
                os.environ[svc.api_key_env] = manual_key
                admin_svc.persist_keys_to_secret({svc.api_key_env: manual_key})
                self._json_response(200, {"status": "set", "service": svc_id, "env": svc.api_key_env})
                return
            # Auto-discover: try file, then HTTP
            config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
            key = read_api_key_from_file(svc_id, config_root)
            source = "config_file"
            if not key:
                key = read_api_key_via_http(svc_id)
                source = "http"
            if key:
                os.environ[svc.api_key_env] = key
                admin_svc.persist_keys_to_secret({svc.api_key_env: key})
                self._json_response(200, {"status": "discovered", "service": svc_id, "source": source})
            else:
                self._json_response(404, {"error": f"Could not discover API key for {svc_id}. Provide it manually via api_key field."})
            return

        # POST /api/routing
        if self.path == "/api/routing":
            body = self._read_json_body()
            if not body:
                self._json_response(400, {"error": "JSON body required"})
                return
            self._json_response(200, config_svc.update_routing(body, self.action_trigger))
            return

        # POST /api/restore — restore config from backup JSON
        if self.path == "/api/restore":
            body = self._read_json_body()
            if not body or "service_configs" not in body:
                self._json_response(400, {"error": "backup JSON with service_configs required"})
                return
            self._json_response(200, config_svc.restore_backup(body))
            return

        # POST /api/jellyfin/reset — hard-reset Jellyfin credentials via DB
        if self.path == "/api/jellyfin/reset":
            body = self._read_json_body()
            username = body.get("username", os.environ.get("STACK_ADMIN_USERNAME", "admin"))
            password = body.get("password", os.environ.get("STACK_ADMIN_PASSWORD", "media-stack"))
            if not password or len(password) < 4:
                self._json_response(400, {"error": "password required (min 4 chars)"})
                return
            self._json_response(200, admin_svc.jellyfin_hard_reset(username, password))
            return

        # POST /api/gpu/enable — auto-configure GPU transcoding in Jellyfin
        if self.path == "/api/gpu/enable":
            self._json_response(200, ops_svc.enable_gpu_transcoding())
            return

        # POST /api/snapshot — take a config snapshot now
        if self.path == "/api/snapshot":
            self._json_response(200, ops_svc.take_snapshot())
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

        # POST /cancel or POST /actions/cancel — cancel running action
        if self.path in ("/cancel", "/actions/cancel"):
            cancelled = self.state.cancel_action()
            self._json_response(200, {
                "status": "cancel_requested" if cancelled else "no_action_running",
                "current_action": self.state.current_action.to_dict() if self.state.current_action else None,
            })
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
