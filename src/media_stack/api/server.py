"""Lightweight HTTP API server for bootstrap runner telemetry and control."""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .state import BootstrapState

logger = logging.getLogger("controller_api")

ActionTriggerFn = Callable[[str, dict[str, Any]], None]

_IMAGE_CONFIG = "/opt/media-stack/contracts/media-stack.config.json"
_IMAGE_PROFILE = "/opt/media-stack/contracts/media-stack.profile.yaml"


def _resolve_config_path(candidate: str | None = None) -> str | None:
    """Resolve bootstrap config JSON path, trying multiple locations."""
    import os
    from pathlib import Path

    candidates = [
        candidate,
        os.environ.get("BOOTSTRAP_CONFIG_FILE"),
        _IMAGE_CONFIG,
    ]
    for p in candidates:
        if p and Path(p).is_file():
            return p
    return None


def _resolve_profile_path(candidate: str | None = None) -> str | None:
    """Resolve bootstrap profile YAML path, trying multiple locations."""
    import os
    from pathlib import Path

    candidates = [
        candidate,
        os.environ.get("BOOTSTRAP_PROFILE_FILE"),
        _IMAGE_PROFILE,
    ]
    for p in candidates:
        if p and Path(p).is_file():
            return p
    return None


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

# Persistent health history file for SLA data that survives restarts.
_HEALTH_HISTORY_PATH = __import__("pathlib").Path("/tmp/media-stack-health-history.json")


def _fire_webhooks(state: BootstrapState, event: str, payload: dict[str, Any]) -> None:
    """POST JSON to all registered webhook URLs (fire-and-forget)."""
    urls = list(state.webhook_urls)
    if not urls:
        return
    body = json.dumps({"event": event, **payload}, default=str).encode("utf-8")
    for url in urls:
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=8)
        except Exception as exc:
            logger.debug("Webhook delivery failed for %s: %s", url, exc)

KNOWN_ACTIONS = frozenset({
    "bootstrap",
    "finalize",
    "auto-indexers",
    "restart-apps",
    "sync-indexers",
    "envoy-config",
    "reconcile",
})

# Load dashboard HTML from file at import time.
_DASHBOARD_HTML_PATH = __import__("pathlib").Path(__file__).with_name("dashboard.html")
_DASHBOARD_HTML = _DASHBOARD_HTML_PATH.read_text(encoding="utf-8") if _DASHBOARD_HTML_PATH.exists() else """<!DOCTYPE html><html><head><title>Media Stack Controller</title></head><body><h1>Media Stack Controller</h1><p>Dashboard file not found. <a href="/status">View raw status</a>.</p></body></html>"""


_API_DOCS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Media Stack Controller — API Reference</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0f1923;color:#e0e0e0;margin:0;padding:0}
header{background:#162230;padding:16px 24px;border-bottom:1px solid #234}
header h1{margin:0;font-size:1.3em;color:#4ade80}
header a{color:#94a3b8;text-decoration:none;font-size:0.9em}
.container{max-width:900px;margin:0 auto;padding:20px}
.endpoint{background:#162230;border:1px solid #1e3044;border-radius:10px;padding:16px;margin:16px 0}
.method{display:inline-block;padding:3px 10px;border-radius:4px;font-weight:bold;font-size:0.85em;margin-right:8px}
.GET{background:#3b82f6;color:#fff}.POST{background:#4ade80;color:#000}.DELETE{background:#ef4444;color:#fff}
.path{font-family:monospace;font-size:1.05em;color:#fff}
.desc{color:#94a3b8;margin:8px 0 0;font-size:0.92em}
pre{background:#0b1219;padding:12px;border-radius:6px;font-size:0.82em;overflow-x:auto;margin:8px 0}
code{color:#fbbf24}
h2{color:#94a3b8;font-size:1.1em;margin:24px 0 8px;padding-top:16px;border-top:1px solid #1e3044}
</style></head><body>
<header><h1>Media Stack Controller &mdash; API Reference</h1>
<a href="/">&larr; Back to Dashboard</a></header>
<div class="container">

<h2>Health &amp; Status</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/healthz</span>
<div class="desc">Liveness probe. Always returns 200 if the service is running.</div>
<pre>curl http://localhost:9100/healthz
<code>{"status": "ok"}</code></pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/readyz</span>
<div class="desc">Readiness probe. Returns initial bootstrap status.</div>
<pre>curl http://localhost:9100/readyz
<code>{"status": "ready", "initial_bootstrap_done": true, "phase": "complete"}</code></pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/api/health</span>
<div class="desc">Live connectivity probe for all services. Tests HTTP reachability in parallel.</div>
<pre>curl http://localhost:9100/api/health
<code>{"services": {"jellyfin": {"status": "ok", "code": 200}, ...}, "healthy": 16, "total": 16}</code></pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/status</span>
<div class="desc">Full controller state: phase, preflight results, app status, action history, runtime config.</div>
<pre>curl http://localhost:9100/status</pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/apps</span>
<div class="desc">App-level status for all configured services.</div>
<pre>curl http://localhost:9100/apps</pre></div>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/apps/{name}</span>
<div class="desc">Status for a specific app (e.g. <code>sonarr</code>, <code>jellyfin</code>).</div>
<pre>curl http://localhost:9100/apps/sonarr</pre></div>

<h2>Actions</h2>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/bootstrap</span>
<div class="desc"><b>Configure All Apps.</b> Runs full pipeline: preflight checks, app wiring, post-bootstrap. Idempotent.</div>
<pre>curl -X POST http://localhost:9100/actions/bootstrap \\
  -H "Content-Type: application/json" -d '{}'

# With retry on failure:
curl -X POST http://localhost:9100/actions/bootstrap \\
  -H "Content-Type: application/json" -d '{"retry": 2}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/auto-indexers</span>
<div class="desc"><b>Discover Indexers.</b> Scans and tests Prowlarr indexer presets, adds working ones.</div>
<pre>curl -X POST http://localhost:9100/actions/auto-indexers \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/envoy-config</span>
<div class="desc"><b>Rebuild Routing.</b> Regenerates Envoy gateway config from profile and service discovery.</div>
<pre>curl -X POST http://localhost:9100/actions/envoy-config \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/restart-apps</span>
<div class="desc"><b>Restart All Apps.</b> Rolling restart of all managed services to pick up config changes.</div>
<pre>curl -X POST http://localhost:9100/actions/restart-apps \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/sync-indexers</span>
<div class="desc"><b>Sync Indexers.</b> Triggers Prowlarr ApplicationIndexerSync to push indexers to Arr apps.</div>
<pre>curl -X POST http://localhost:9100/actions/sync-indexers \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/actions/reconcile</span>
<div class="desc"><b>Reconcile.</b> Re-runs bootstrap to fix drift (same as bootstrap but intended for periodic use).</div>
<pre>curl -X POST http://localhost:9100/actions/reconcile \\
  -H "Content-Type: application/json" -d '{}'</pre></div>

<h2>Configuration</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/config</span>
<div class="desc">Current runtime config overrides.</div>
<pre>curl http://localhost:9100/config
<code>{"config": {"auto_download_content": false}}</code></pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/config</span>
<div class="desc">Update runtime config. Changes take effect on next action run.</div>
<pre># Enable auto-downloads:
curl -X POST http://localhost:9100/config \\
  -H "Content-Type: application/json" \\
  -d '{"auto_download_content": true}'

# Disable auto-downloads:
curl -X POST http://localhost:9100/config \\
  -H "Content-Type: application/json" \\
  -d '{"auto_download_content": false}'</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/reload</span>
<div class="desc">Reload bootstrap profile YAML and re-apply environment config.</div>
<pre>curl -X POST http://localhost:9100/reload</pre></div>

<h2>Logs &amp; Monitoring</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/logs/stream</span>
<div class="desc">Server-Sent Events (SSE) stream of real-time log lines. Connect from browser or CLI.</div>
<pre># Browser: open http://localhost:9100/logs/stream
# CLI:
curl -N http://localhost:9100/logs/stream

# Resume from a specific sequence number:
curl -N "http://localhost:9100/logs/stream?after_seq=42"</pre></div>

<h2>Webhooks</h2>

<div class="endpoint">
<span class="method GET">GET</span><span class="path">/webhooks</span>
<div class="desc">List registered webhook URLs.</div>
<pre>curl http://localhost:9100/webhooks</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/webhooks</span>
<div class="desc">Register a webhook URL. Receives JSON POST on action completion/error.</div>
<pre>curl -X POST http://localhost:9100/webhooks \\
  -H "Content-Type: application/json" \\
  -d '{"url": "https://hooks.example.com/media-stack"}'</pre></div>

<div class="endpoint">
<span class="method DELETE">DELETE</span><span class="path">/webhooks</span>
<div class="desc">Remove a registered webhook URL.</div>
<pre>curl -X DELETE http://localhost:9100/webhooks \\
  -H "Content-Type: application/json" \\
  -d '{"url": "https://hooks.example.com/media-stack"}'</pre></div>

<h2>Lifecycle</h2>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/reset</span>
<div class="desc">Reset controller state to idle (only when no action is running).</div>
<pre>curl -X POST http://localhost:9100/reset</pre></div>

<div class="endpoint">
<span class="method POST">POST</span><span class="path">/cancel</span>
<div class="desc">Cancel the currently running action.</div>
<pre>curl -X POST http://localhost:9100/cancel</pre></div>

</div></body></html>"""


class BootstrapAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for bootstrap lifecycle and action endpoints."""

    state: BootstrapState
    action_trigger: ActionTriggerFn | None = None
    reload_config: Callable[[], None] | None = None

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        logger.debug("[%s] %s %s", ts, self.command, self.path)

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

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _sse_response(self) -> None:
        """Send Server-Sent Events stream of log lines."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        # Parse ?after_seq=N from query string.
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
                for seq, ts, msg in entries:
                    ts_str = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))
                    data = json.dumps({"seq": seq, "ts": ts_str, "msg": msg})
                    self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode())
                    after_seq = seq
                self.wfile.flush()
                # Block until next log line or timeout (long-poll).
                self.state.wait_for_log(timeout=30.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    _SERVICE_PROBES: dict[str, tuple[str, int, str]] = {
        "jellyfin": ("jellyfin", 8096, "/System/Info/Public"),
        "jellyseerr": ("jellyseerr", 5055, "/api/v1/status"),
        "sonarr": ("sonarr", 8989, "/ping"),
        "radarr": ("radarr", 7878, "/ping"),
        "lidarr": ("lidarr", 8686, "/ping"),
        "readarr": ("readarr", 8787, "/ping"),
        "prowlarr": ("prowlarr", 9696, "/ping"),
        "qbittorrent": ("qbittorrent", 8080, "/"),
        "sabnzbd": ("sabnzbd", 8080, "/"),
        "bazarr": ("bazarr", 6767, "/"),
        "maintainerr": ("maintainerr", 6246, "/app/maintainerr/api/settings"),
        "tautulli": ("tautulli", 8181, "/status"),
        "homepage": ("homepage", 3000, "/"),
        "envoy": ("envoy", 9901, "/ready"),
        "plex": ("plex", 32400, "/identity"),
        "flaresolverr": ("flaresolverr", 8191, "/"),
    }

    # Authenticated API endpoints used to validate API keys.
    # Maps service name → (auth_path, key_header_or_mode).
    # "X-Api-Key" = send key as header; "query:apikey" = send as query param.
    _AUTH_PROBES: dict[str, tuple[str, int, str, str]] = {
        "sonarr": ("sonarr", 8989, "/api/v3/system/status", "X-Api-Key"),
        "radarr": ("radarr", 7878, "/api/v3/system/status", "X-Api-Key"),
        "lidarr": ("lidarr", 8686, "/api/v1/system/status", "X-Api-Key"),
        "readarr": ("readarr", 8787, "/api/v1/system/status", "X-Api-Key"),
        "prowlarr": ("prowlarr", 9696, "/api/v1/system/status", "X-Api-Key"),
        "bazarr": ("bazarr", 6767, "/api/system/status", "X-Api-Key"),
        "jellyfin": ("jellyfin", 8096, "/System/Info", "X-Emby-Token"),
        "jellyseerr": ("jellyseerr", 5055, "/api/v1/settings/main", "X-Api-Key"),
        "sabnzbd": ("sabnzbd", 8080, "/api", "query:apikey"),
    }

    @staticmethod
    def _discover_api_keys() -> dict[str, str]:
        """Read API keys from app config files on disk."""
        import os
        import re
        from pathlib import Path

        config_root = Path(os.environ.get("CONFIG_ROOT", "/srv-config"))
        keys: dict[str, str] = {}

        # Arr apps + Prowlarr — XML config
        for app in ("sonarr", "radarr", "lidarr", "readarr", "prowlarr"):
            xml = config_root / app / "config.xml"
            if xml.exists():
                text = xml.read_text(encoding="utf-8", errors="replace")
                m = re.search(r"<ApiKey>([^<]+)</ApiKey>", text)
                if m:
                    keys[app] = m.group(1).strip()

        # SABnzbd — INI
        sab_ini = config_root / "sabnzbd" / "sabnzbd.ini"
        if sab_ini.exists():
            text = sab_ini.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^\s*api_key\s*=\s*(\S+)", text, re.MULTILINE)
            if m:
                keys["sabnzbd"] = m.group(1).strip()

        # Bazarr — YAML (uses shared reader)
        from media_stack.api.preflight.api_keys import _read_bazarr_api_key
        bazarr_cfg = config_root / "bazarr" / "config" / "config.yaml"
        bazarr_key = _read_bazarr_api_key(bazarr_cfg)
        if bazarr_key:
            keys["bazarr"] = bazarr_key

        # Jellyseerr — JSON settings
        js_settings = config_root / "jellyseerr" / "settings.json"
        if js_settings.exists():
            try:
                data = json.loads(js_settings.read_text(encoding="utf-8", errors="replace"))
                api_key = str((data.get("main") or {}).get("apiKey", "")).strip()
                if api_key:
                    keys["jellyseerr"] = api_key
            except Exception:
                pass

        # Jellyfin — SQLite db
        import sqlite3
        jf_db = config_root / "jellyfin" / "data" / "jellyfin.db"
        if jf_db.exists():
            try:
                conn = sqlite3.connect(f"file:{jf_db}?mode=ro", uri=True)
                cur = conn.cursor()
                cur.execute("SELECT AccessToken FROM ApiKeys ORDER BY Id DESC LIMIT 1")
                row = cur.fetchone()
                conn.close()
                if row and row[0]:
                    keys["jellyfin"] = str(row[0]).strip()
            except Exception:
                pass

        return keys

    def _probe_services(self) -> dict[str, Any]:
        """Probe all known services: reachability + authenticated API validation."""
        cached = _api_cache.get("health", 10)
        if cached is not None:
            return cached
        from concurrent.futures import ThreadPoolExecutor, as_completed

        api_keys = self._discover_api_keys()

        def probe(name: str) -> tuple[str, dict[str, Any]]:
            host, port, path = self._SERVICE_PROBES[name]
            url = f"http://{host}:{port}{path}"
            t0 = time.time()
            result: dict[str, Any] = {"url": url}
            try:
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=4) as resp:
                    result["status"] = "ok"
                    result["code"] = resp.status
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    result["status"] = "ok"
                    result["code"] = exc.code
                else:
                    result["status"] = "error"
                    result["code"] = exc.code
            except Exception as exc:
                result["status"] = "error"
                result["error"] = str(exc)[:80]
            result["ms"] = round((time.time() - t0) * 1000)

            # Authenticated probe if we have an API key for this service.
            key = api_keys.get(name)
            if key and name in self._AUTH_PROBES:
                a_host, a_port, a_path, a_mode = self._AUTH_PROBES[name]
                if a_mode.startswith("query:"):
                    param = a_mode.split(":", 1)[1]
                    a_url = f"http://{a_host}:{a_port}{a_path}?{param}={key}&output=json&mode=version"
                    headers: dict[str, str] = {}
                else:
                    a_url = f"http://{a_host}:{a_port}{a_path}"
                    headers = {a_mode: key}
                try:
                    req = urllib.request.Request(a_url, method="GET", headers=headers)
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        result["auth"] = "ok"
                except urllib.error.HTTPError as exc:
                    result["auth"] = "unauthorized" if exc.code in (401, 403) else "error"
                except Exception:
                    result["auth"] = "error"
            elif name in self._AUTH_PROBES:
                result["auth"] = "no_key"
            else:
                # Services that don't use API keys (homepage, envoy, etc.)
                result["auth"] = "n/a"

            return name, result

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(probe, name): name for name in self._SERVICE_PROBES}
            for f in as_completed(futures):
                name, result = f.result()
                results[name] = result

        ok_count = sum(1 for v in results.values() if v["status"] == "ok")
        auth_ok = sum(1 for v in results.values() if v.get("auth") == "ok")
        response = {"services": results, "healthy": ok_count, "authenticated": auth_ok, "total": len(results)}
        _api_cache.set("health", response)
        # Persist health snapshot for SLA history (#2)
        self._append_health_history(results)
        return response

    def _append_health_history(self, services: dict[str, Any]) -> None:
        """Append a health snapshot to persistent history file."""
        try:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S")
            snapshot = {s: v["status"] for s, v in services.items()}
            history: list[dict[str, Any]] = []
            if _HEALTH_HISTORY_PATH.exists():
                try:
                    history = json.loads(_HEALTH_HISTORY_PATH.read_text())
                except Exception:
                    history = []
            history.append({"ts": ts, "services": snapshot})
            # Keep last 2880 entries (~24h at 30s intervals)
            if len(history) > 2880:
                history = history[-2880:]
            _HEALTH_HISTORY_PATH.write_text(json.dumps(history))
        except Exception:
            pass

    def _get_health_history(self) -> dict[str, Any]:
        """Read persistent health history for SLA calculations."""
        try:
            if _HEALTH_HISTORY_PATH.exists():
                history = json.loads(_HEALTH_HISTORY_PATH.read_text())
                # Calculate SLA per service
                sla: dict[str, dict[str, Any]] = {}
                for svc in self._SERVICE_PROBES:
                    checks = [h["services"].get(svc) for h in history if svc in h.get("services", {})]
                    if checks:
                        ok = sum(1 for c in checks if c == "ok")
                        sla[svc] = {"checks": len(checks), "ok": ok,
                                    "pct": round(ok / len(checks) * 100, 2)}
                return {"history_count": len(history), "sla": sla,
                        "oldest": history[0]["ts"] if history else None,
                        "newest": history[-1]["ts"] if history else None}
            return {"history_count": 0, "sla": {}}
        except Exception as exc:
            return {"error": str(exc)[:80]}

    def _get_versions(self) -> dict[str, Any]:
        """Query each service API for its version string."""
        cached = _api_cache.get("versions", 300)
        if cached is not None:
            return cached
        from concurrent.futures import ThreadPoolExecutor, as_completed

        api_keys = self._discover_api_keys()
        version_endpoints: dict[str, tuple[str, int, str, str, str]] = {
            # name: (host, port, path, auth_header, version_json_path)
            "sonarr": ("sonarr", 8989, "/api/v3/system/status", "X-Api-Key", "version"),
            "radarr": ("radarr", 7878, "/api/v3/system/status", "X-Api-Key", "version"),
            "lidarr": ("lidarr", 8686, "/api/v1/system/status", "X-Api-Key", "version"),
            "readarr": ("readarr", 8787, "/api/v1/system/status", "X-Api-Key", "version"),
            "prowlarr": ("prowlarr", 9696, "/api/v1/system/status", "X-Api-Key", "version"),
            "bazarr": ("bazarr", 6767, "/api/system/status", "X-Api-Key", "data.bazarr_version"),
            "jellyfin": ("jellyfin", 8096, "/System/Info/Public", "", "Version"),
            "jellyseerr": ("jellyseerr", 5055, "/api/v1/status", "X-Api-Key", "version"),
            "tautulli": ("tautulli", 8181, "/api/v2?cmd=get_tautulli_info&apikey=", "", "response.data.tautulli_version"),
            "plex": ("plex", 32400, "/identity", "", "MediaContainer.version"),
        }

        def fetch_version(name: str) -> tuple[str, str]:
            if name not in version_endpoints:
                return name, ""
            host, port, path, auth_hdr, json_path = version_endpoints[name]
            key = api_keys.get(name, "")
            headers: dict[str, str] = {}
            if auth_hdr and key:
                headers[auth_hdr] = key
            url = f"http://{host}:{port}{path}"
            try:
                req = urllib.request.Request(url, method="GET", headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                # Navigate dotted json_path like "response.data.version"
                for segment in json_path.split("."):
                    if isinstance(data, dict):
                        data = data.get(segment, "")
                    else:
                        data = ""
                        break
                return name, str(data) if data else ""
            except Exception:
                return name, ""

        results: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fetch_version, name) for name in version_endpoints]
            for f in as_completed(futures):
                name, version = f.result()
                if version:
                    results[name] = version
        result = {"versions": results}
        _api_cache.set("versions", result)
        return result

    def _get_downloads(self) -> dict[str, Any]:
        """Query qBittorrent and SABnzbd for active downloads."""
        api_keys = self._discover_api_keys()
        downloads: dict[str, Any] = {}

        # qBittorrent — get torrent list
        try:
            url = "http://qbittorrent:8080/api/v2/torrents/info?filter=active"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                torrents = json.loads(resp.read().decode())
            active = [t for t in torrents if t.get("state") in ("downloading", "uploading", "stalledDL", "forcedDL")]
            downloads["qbittorrent"] = {
                "active": len(active),
                "total": len(torrents),
                "items": [
                    {"name": t.get("name", "")[:60], "progress": round(t.get("progress", 0) * 100, 1),
                     "size": t.get("size", 0), "dlspeed": t.get("dlspeed", 0), "state": t.get("state", "")}
                    for t in active[:10]
                ],
            }
        except Exception as exc:
            downloads["qbittorrent"] = {"error": str(exc)[:80]}

        # SABnzbd — get queue
        sab_key = api_keys.get("sabnzbd", "")
        if sab_key:
            try:
                url = f"http://sabnzbd:8080/api?mode=queue&output=json&apikey={sab_key}"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                q = data.get("queue", {})
                slots = q.get("slots", [])
                downloads["sabnzbd"] = {
                    "active": len(slots),
                    "speed": q.get("speed", "0"),
                    "items": [
                        {"name": s.get("filename", "")[:60], "progress": float(s.get("percentage", 0)),
                         "size": s.get("size", ""), "status": s.get("status", "")}
                        for s in slots[:10]
                    ],
                }
            except Exception as exc:
                downloads["sabnzbd"] = {"error": str(exc)[:80]}

        return downloads

    def _get_stats(self) -> dict[str, Any]:
        """Query arr apps for library counts."""
        cached = _api_cache.get("stats", 60)
        if cached is not None:
            return cached
        from concurrent.futures import ThreadPoolExecutor, as_completed

        api_keys = self._discover_api_keys()
        stats_endpoints: dict[str, tuple[str, int, str, str]] = {
            "sonarr": ("sonarr", 8989, "/api/v3/series", "series"),
            "radarr": ("radarr", 7878, "/api/v3/movie", "movies"),
            "lidarr": ("lidarr", 8686, "/api/v1/artist", "artists"),
            "readarr": ("readarr", 8787, "/api/v1/book", "books"),
        }

        def fetch_count(name: str) -> tuple[str, dict[str, Any]]:
            host, port, path, label = stats_endpoints[name]
            key = api_keys.get(name, "")
            if not key:
                return name, {"count": 0, "label": label, "error": "no_key"}
            try:
                url = f"http://{host}:{port}{path}"
                req = urllib.request.Request(url, headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                return name, {"count": len(data) if isinstance(data, list) else 0, "label": label}
            except Exception as exc:
                return name, {"count": 0, "label": label, "error": str(exc)[:80]}

        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch_count, n) for n in stats_endpoints]
            for f in as_completed(futures):
                name, data = f.result()
                results[name] = data
        result = {"stats": results}
        _api_cache.set("stats", result)
        return result

    def _get_indexers(self) -> dict[str, Any]:
        """Query Prowlarr for indexer status."""
        api_keys = self._discover_api_keys()
        key = api_keys.get("prowlarr", "")
        if not key:
            return {"indexers": [], "error": "no_key"}
        try:
            url = "http://prowlarr:9696/api/v1/indexer"
            req = urllib.request.Request(url, headers={"X-Api-Key": key})
            with urllib.request.urlopen(req, timeout=5) as resp:
                indexers = json.loads(resp.read().decode())
            result = []
            for idx in indexers:
                result.append({
                    "id": idx.get("id"),
                    "name": idx.get("name", ""),
                    "implementation": idx.get("implementation", ""),
                    "enabled": idx.get("enable", False),
                    "priority": idx.get("priority", 25),
                })
            return {"indexers": result, "total": len(result), "enabled": sum(1 for i in result if i["enabled"])}
        except Exception as exc:
            return {"indexers": [], "error": str(exc)[:80]}

    def _get_disk(self) -> dict[str, Any]:
        """Check disk usage on media/config volumes."""
        import os
        from pathlib import Path
        from shutil import disk_usage

        # Auto-detect volume paths: check common locations.
        paths_to_check: dict[str, str] = {
            "config": os.environ.get("CONFIG_ROOT", "/srv-config"),
        }
        # Media volume — check several common locations.
        for label, candidates in [
            ("media", [os.environ.get("MEDIA_ROOT", ""), "/srv-stack/media", "/media", "/data/media"]),
            ("torrents", ["/srv-stack/data/torrents", "/data/torrents", "/downloads/torrents"]),
            ("usenet", ["/srv-stack/data/usenet", "/data/usenet", "/downloads/usenet"]),
        ]:
            for p in candidates:
                if p and Path(p).exists():
                    paths_to_check[label] = p
                    break
        results: dict[str, Any] = {}
        for label, path_str in paths_to_check.items():
            path = Path(path_str)
            if path.exists():
                try:
                    usage = disk_usage(path)
                    results[label] = {
                        "path": str(path),
                        "total_bytes": usage.total,
                        "used_bytes": usage.used,
                        "free_bytes": usage.free,
                        "percent_used": round(usage.used / usage.total * 100, 1) if usage.total else 0,
                    }
                except Exception as exc:
                    results[label] = {"path": str(path), "error": str(exc)[:80]}
            else:
                results[label] = {"path": str(path), "error": "path not found"}
        # Include disk guardrail thresholds from bootstrap config.
        guardrails: dict[str, Any] = {"enabled": False}
        resolved_cfg = _resolve_config_path()
        if resolved_cfg:
            try:
                import json as _json
                cfg = _json.loads(Path(resolved_cfg).read_text(encoding="utf-8"))
                gc = cfg.get("disk_guardrails") or {}
                guardrails = {
                    "enabled": bool(gc.get("enabled", False)),
                    "max_used_percent": float(gc.get("max_used_percent", 65)),
                    "target_used_percent": float(gc.get("target_used_percent", 58)),
                    "monitor_path": str(gc.get("monitor_path", "")),
                    "qbit_cleanup": {
                        "enabled": bool((gc.get("qbit_cleanup") or {}).get("enabled", True)),
                        "min_completion_age_hours": float((gc.get("qbit_cleanup") or {}).get("min_completion_age_hours", 36)),
                        "min_ratio": float((gc.get("qbit_cleanup") or {}).get("min_ratio", 1.0)),
                        "min_seeding_time_minutes": int((gc.get("qbit_cleanup") or {}).get("min_seeding_time_minutes", 720)),
                        "max_delete_per_run": int((gc.get("qbit_cleanup") or {}).get("max_delete_per_run", 80)),
                        "delete_files": bool((gc.get("qbit_cleanup") or {}).get("delete_files", True)),
                        "categories": list((gc.get("qbit_cleanup") or {}).get("categories", [])),
                    },
                }
            except Exception:
                pass
        return {"disk": results, "guardrails": guardrails}

    def _get_routing(self) -> dict[str, Any]:
        """Return current routing configuration from profile."""
        import os
        from pathlib import Path

        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        resolved = _resolve_profile_path(profile_file)
        profile_path = Path(resolved) if resolved else None
        routing: dict[str, Any] = {}
        if profile_path and profile_path.is_file():
            try:
                import yaml
                with open(profile_path) as f:
                    profile = yaml.safe_load(f) or {}
                routing = profile.get("routing") or {}
            except Exception:
                pass
        return {
            "base_domain": str(routing.get("base_domain", "local")),
            "stack_subdomain": str(routing.get("stack_subdomain", "media-stack")),
            "gateway_host": str(routing.get("gateway_host", "apps.media-stack.local")),
            "gateway_port": int(routing.get("gateway_port", 80)),
            "app_path_prefix": str(routing.get("app_path_prefix", "/app")),
            "strategy": str(routing.get("strategy", "hybrid")),
            "internet_exposed": bool(routing.get("internet_exposed", False)),
            "direct_hosts": dict(routing.get("direct_hosts") or {}),
        }

    def _get_env(self) -> dict[str, Any]:
        """Return runtime environment information."""
        import os
        import platform
        from pathlib import Path

        namespace = os.environ.get("K8S_NAMESPACE", "")
        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        profile_name = ""
        if profile_file:
            p = Path(profile_file)
            if p.exists():
                profile_name = p.name

        # Discover node IP for constructing external URLs.
        node_ip = os.environ.get("NODE_IP", "")
        if not node_ip:
            import socket
            try:
                node_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                node_ip = ""

        # Discover ingress hosts if on K8s.
        ingress_hosts: dict[str, str] = {}
        gateway_nodeport = 0
        if namespace:
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                net_v1 = k8s_client.NetworkingV1Api()
                ingresses = net_v1.list_namespaced_ingress(namespace)
                for ing in ingresses.items:
                    for rule in (ing.spec.rules or []):
                        host = rule.host or ""
                        if host and rule.http:
                            for path in rule.http.paths:
                                svc_name = path.backend.service.name
                                ingress_hosts[svc_name] = host
                # Get envoy NodePort for gateway access.
                core_v1 = k8s_client.CoreV1Api()
                try:
                    envoy_svc = core_v1.read_namespaced_service("envoy", namespace)
                    for port in (envoy_svc.spec.ports or []):
                        if port.name == "http" and port.node_port:
                            gateway_nodeport = port.node_port
                except Exception:
                    pass
            except Exception:
                pass

        return {
            "runtime": "kubernetes" if namespace else "compose",
            "namespace": namespace,
            "config_root": os.environ.get("CONFIG_ROOT", "/srv-config"),
            "profile_file": profile_file,
            "profile_name": profile_name,
            "hostname": platform.node(),
            "python_version": platform.python_version(),
            "api_port": int(os.environ.get("BOOTSTRAP_API_PORT", "9100")),
            "env": os.environ.get("MEDIA_STACK_ENV", "prod"),
            "gateway_host": os.environ.get("APP_GATEWAY_HOST", ""),
            "route_strategy": os.environ.get("ROUTE_STRATEGY", ""),
            "node_ip": node_ip,
            "gateway_nodeport": gateway_nodeport,
            "ingress_hosts": ingress_hosts,
        }

    def _get_recent(self) -> dict[str, Any]:
        """Query arr apps for recently added items."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        api_keys = self._discover_api_keys()
        recent_endpoints: dict[str, tuple[str, int, str, str, str]] = {
            # name: (host, port, path, title_field, date_field)
            "sonarr": ("sonarr", 8989, "/api/v3/series?sortKey=dateAdded&sortDirection=descending", "title", "dateAdded"),
            "radarr": ("radarr", 7878, "/api/v3/movie?sortKey=dateAdded&sortDirection=descending", "title", "dateAdded"),
        }

        def fetch_recent(name: str) -> tuple[str, list[dict[str, str]]]:
            host, port, path, title_f, date_f = recent_endpoints[name]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                url = f"http://{host}:{port}{path}"
                req = urllib.request.Request(url, headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                items = data if isinstance(data, list) else []
                return name, [
                    {"title": str(it.get(title_f, ""))[:80], "added": str(it.get(date_f, ""))[:10]}
                    for it in items[:5]
                ]
            except Exception:
                return name, []

        results: dict[str, list[dict[str, str]]] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetch_recent, n) for n in recent_endpoints]
            for f in as_completed(futures):
                name, items = f.result()
                results[name] = items
        return {"recent": results}

    def _get_profile(self) -> dict[str, Any]:
        """Read and return the bootstrap profile YAML."""
        import os
        from pathlib import Path

        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        resolved = _resolve_profile_path(profile_file)
        if not resolved:
            return {"profile": None, "error": f"Profile not found (tried {profile_file}, image default)"}
        path = Path(resolved)
        try:
            import yaml
            with open(path) as f:
                profile = yaml.safe_load(f) or {}
            return {"profile": profile, "file": str(path)}
        except ImportError:
            return {"profile_raw": path.read_text(encoding="utf-8"), "file": str(path)}
        except Exception as exc:
            return {"profile": None, "error": str(exc)[:120]}

    def _restart_service(self, service_name: str) -> dict[str, Any]:
        """Restart a single service container or pod."""
        import os

        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            # Kubernetes: patch deployment to trigger rollout restart
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                apps_v1 = k8s_client.AppsV1Api()
                patch = {"spec": {"template": {"metadata": {"annotations": {
                    "bootstrap.media-stack.io/restart-trigger": str(int(time.time()))
                }}}}}
                apps_v1.patch_namespaced_deployment(service_name, namespace, body=patch)
                return {"status": "restarted", "service": service_name, "method": "k8s-rollout"}
            except Exception as exc:
                return {"status": "error", "service": service_name, "error": str(exc)[:120]}
        else:
            # Docker Compose: restart container by name
            try:
                import docker
                client = docker.from_env()
                container = client.containers.get(service_name)
                container.restart(timeout=30)
                return {"status": "restarted", "service": service_name, "method": "docker-restart"}
            except Exception as exc:
                return {"status": "error", "service": service_name, "error": str(exc)[:120]}

    def _test_webhook(self) -> dict[str, Any]:
        """Send a test payload to all registered webhooks."""
        urls = list(self.state.webhook_urls)
        if not urls:
            return {"status": "no_webhooks", "tested": 0}
        test_payload = json.dumps({
            "event": "test",
            "action": "webhook_test",
            "status": "ok",
            "message": "This is a test webhook from Media Stack Controller.",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }).encode("utf-8")
        results: list[dict[str, Any]] = []
        for url in urls:
            try:
                req = urllib.request.Request(
                    url, data=test_payload, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    results.append({"url": url, "status": "ok", "code": resp.status})
            except Exception as exc:
                results.append({"url": url, "status": "error", "error": str(exc)[:80]})
        return {"tested": len(results), "results": results}

    def _get_envoy_stats(self) -> dict[str, Any]:
        """Query Envoy admin for traffic stats."""
        try:
            req = urllib.request.Request("http://envoy:9901/stats?format=json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            stats = data.get("stats", [])
            result: dict[str, Any] = {"clusters": {}, "listener": {}}
            for s in stats:
                name = s.get("name", "")
                val = s.get("value", 0)
                # Per-cluster (service) stats
                for svc in self._SERVICE_PROBES:
                    prefix = f"cluster.{svc}."
                    if name.startswith(prefix):
                        if svc not in result["clusters"]:
                            result["clusters"][svc] = {}
                        key = name[len(prefix):]
                        if key in ("upstream_rq_total", "upstream_rq_2xx", "upstream_rq_4xx",
                                   "upstream_rq_5xx", "upstream_cx_total", "upstream_cx_active",
                                   "upstream_rq_time"):
                            result["clusters"][svc][key] = val
                # Listener stats
                if name.startswith("listener.") and ("downstream_cx" in name or "downstream_rq" in name):
                    result["listener"][name] = val
            return result
        except Exception as exc:
            return {"error": str(exc)[:80]}

    def _get_download_history(self) -> dict[str, Any]:
        """Get recent download history from arr apps."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        api_keys = self._discover_api_keys()
        endpoints = {
            "sonarr": ("sonarr", 8989, "/api/v3/history?pageSize=10&sortKey=date&sortDirection=descending"),
            "radarr": ("radarr", 7878, "/api/v3/history?pageSize=10&sortKey=date&sortDirection=descending"),
        }
        def fetch(name: str) -> tuple[str, list[dict[str, str]]]:
            host, port, path = endpoints[name]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                url = f"http://{host}:{port}{path}"
                req = urllib.request.Request(url, headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                records = data.get("records", data) if isinstance(data, dict) else data
                if not isinstance(records, list):
                    records = []
                return name, [{"title": str(r.get("sourceTitle", ""))[:80],
                               "quality": str(r.get("quality", {}).get("quality", {}).get("name", "")),
                               "date": str(r.get("date", ""))[:19],
                               "event": str(r.get("eventType", ""))}
                              for r in records[:10]]
            except Exception:
                return name, []
        results: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for f in as_completed([pool.submit(fetch, n) for n in endpoints]):
                name, items = f.result()
                results[name] = items
        return {"history": results}

    def _get_indexer_stats(self) -> dict[str, Any]:
        """Get indexer performance stats from Prowlarr."""
        api_keys = self._discover_api_keys()
        key = api_keys.get("prowlarr", "")
        if not key:
            return {"error": "no_key"}
        try:
            url = "http://prowlarr:9696/api/v1/indexerstats"
            req = urllib.request.Request(url, headers={"X-Api-Key": key})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            indexers = data.get("indexers", data) if isinstance(data, dict) else data
            if not isinstance(indexers, list):
                return {"indexers": []}
            return {"indexers": [{"name": i.get("indexerName", ""), "queries": i.get("numberOfQueries", 0),
                                  "grabs": i.get("numberOfGrabs", 0), "fails": i.get("numberOfFailedQueries", 0),
                                  "avgResponseTime": i.get("averageResponseTime", 0)}
                                 for i in indexers]}
        except Exception as exc:
            return {"error": str(exc)[:80]}

    def _get_quality_profiles(self) -> dict[str, Any]:
        """Get quality profiles from arr apps."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        api_keys = self._discover_api_keys()
        endpoints = {
            "sonarr": ("sonarr", 8989, "/api/v3/qualityprofile"),
            "radarr": ("radarr", 7878, "/api/v3/qualityprofile"),
        }
        def fetch(name: str) -> tuple[str, list[dict[str, Any]]]:
            host, port, path = endpoints[name]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                url = f"http://{host}:{port}{path}"
                req = urllib.request.Request(url, headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                return name, [{"id": p.get("id"), "name": p.get("name", ""), "cutoff": p.get("cutoff", 0)}
                              for p in (data if isinstance(data, list) else [])]
            except Exception:
                return name, []
        results: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for f in as_completed([pool.submit(fetch, n) for n in endpoints]):
                name, items = f.result()
                results[name] = items
        return {"profiles": results}

    def _get_import_lists(self) -> dict[str, Any]:
        """Get import list status from arr apps."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        api_keys = self._discover_api_keys()
        endpoints = {
            "sonarr": ("sonarr", 8989, "/api/v3/importlist"),
            "radarr": ("radarr", 7878, "/api/v3/importlist"),
        }
        def fetch(name: str) -> tuple[str, list[dict[str, Any]]]:
            host, port, path = endpoints[name]
            key = api_keys.get(name, "")
            if not key:
                return name, []
            try:
                url = f"http://{host}:{port}{path}"
                req = urllib.request.Request(url, headers={"X-Api-Key": key})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                return name, [{"id": il.get("id"), "name": il.get("name", ""),
                               "enabled": il.get("enabled", il.get("enableAutomaticAdd", False)),
                               "type": il.get("listType", il.get("implementation", ""))}
                              for il in (data if isinstance(data, list) else [])]
            except Exception:
                return name, []
        results: dict[str, list] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            for f in as_completed([pool.submit(fetch, n) for n in endpoints]):
                name, items = f.result()
                results[name] = items
        return {"import_lists": results}

    def _get_backup(self) -> bytes:
        """Create a JSON backup of all discoverable config."""
        import os
        from pathlib import Path
        backup: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "env": self._get_env(),
            "state": self.state.to_dict(),
        }
        # Include profile
        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        if profile_file:
            p = Path(profile_file)
            if p.exists():
                backup["profile_raw"] = p.read_text(encoding="utf-8", errors="replace")
        return json.dumps(backup, indent=2, default=str).encode("utf-8")

    def _get_namespaces(self) -> dict[str, Any]:
        """List K8s namespaces with pod details, or compose project info."""
        import os
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                core_v1 = k8s_client.CoreV1Api()
                apps_v1 = k8s_client.AppsV1Api()
                all_ns = core_v1.list_namespace()
                ns_results = []
                for ns in all_ns.items:
                    name = ns.metadata.name
                    if "media" not in name and name != namespace:
                        continue
                    pods = core_v1.list_namespaced_pod(name)
                    running = sum(1 for p in pods.items if p.status.phase == "Running")
                    # Collect problem pods
                    problems = []
                    for p in pods.items:
                        phase = p.status.phase
                        if phase not in ("Running", "Succeeded"):
                            reason = ""
                            if p.status.container_statuses:
                                for cs in p.status.container_statuses:
                                    if cs.state.waiting:
                                        reason = cs.state.waiting.reason or ""
                                    elif cs.state.terminated:
                                        reason = cs.state.terminated.reason or ""
                            problems.append({
                                "name": p.metadata.name,
                                "phase": phase,
                                "reason": reason,
                            })
                    ns_results.append({
                        "namespace": name, "pods": len(pods.items),
                        "running": running, "current": name == namespace,
                        "problems": problems,
                    })

                # Per-service replica info + resource usage for current namespace
                services: list[dict[str, Any]] = []
                try:
                    deployments = apps_v1.list_namespaced_deployment(namespace)
                    for dep in deployments.items:
                        svc: dict[str, Any] = {
                            "name": dep.metadata.name,
                            "replicas": dep.spec.replicas or 0,
                            "ready": dep.status.ready_replicas or 0,
                            "available": dep.status.available_replicas or 0,
                            "image": "",
                        }
                        if dep.spec.template.spec.containers:
                            svc["image"] = dep.spec.template.spec.containers[0].image or ""
                            res = dep.spec.template.spec.containers[0].resources
                            if res:
                                if res.requests:
                                    svc["cpu_request"] = str(res.requests.get("cpu", ""))
                                    svc["mem_request"] = str(res.requests.get("memory", ""))
                                if res.limits:
                                    svc["cpu_limit"] = str(res.limits.get("cpu", ""))
                                    svc["mem_limit"] = str(res.limits.get("memory", ""))
                        services.append(svc)
                except Exception:
                    pass

                # Pod-level resource usage from metrics API (best effort)
                pod_metrics: list[dict[str, Any]] = []
                try:
                    custom = k8s_client.CustomObjectsApi()
                    metrics = custom.list_namespaced_custom_object(
                        "metrics.k8s.io", "v1beta1", namespace, "pods"
                    )
                    for item in metrics.get("items", []):
                        pod_name = item.get("metadata", {}).get("name", "")
                        for c in item.get("containers", []):
                            usage = c.get("usage", {})
                            pod_metrics.append({
                                "pod": pod_name,
                                "container": c.get("name", ""),
                                "cpu": usage.get("cpu", ""),
                                "memory": usage.get("memory", ""),
                            })
                except Exception:
                    pass  # Metrics API may not be available

                return {
                    "namespaces": ns_results,
                    "services": services,
                    "pod_metrics": pod_metrics,
                }
            except Exception as exc:
                return {"error": str(exc)[:120]}
        # Docker Compose: get container stats (parallelized)
        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import docker
            client = docker.from_env()
            containers = client.containers.list(all=True)
            services: list[dict[str, Any]] = []
            running_containers = []
            running = 0
            for c in containers:
                name = c.name
                status = c.status
                is_running = status == "running"
                if is_running:
                    running += 1
                    running_containers.append(c)
                svc: dict[str, Any] = {
                    "name": name,
                    "replicas": 1,
                    "ready": 1 if is_running else 0,
                    "available": 1 if is_running else 0,
                    "image": c.image.tags[0] if c.image.tags else str(c.image.short_id),
                }
                health = c.attrs.get("State", {}).get("Health", {}).get("Status", "")
                if health:
                    svc["health"] = health
                services.append(svc)

            # Parallel stats collection (each call blocks ~1s for CPU delta)
            def _get_container_stats(c: Any) -> dict[str, Any] | None:
                try:
                    stats = c.stats(stream=False)
                    cpu_delta = stats["cpu_stats"]["cpu_usage"]["total_usage"] - stats["precpu_stats"]["cpu_usage"]["total_usage"]
                    sys_delta = stats["cpu_stats"]["system_cpu_usage"] - stats["precpu_stats"]["system_cpu_usage"]
                    ncpus = stats["cpu_stats"].get("online_cpus", 1)
                    cpu_pct = round((cpu_delta / sys_delta) * ncpus * 100, 2) if sys_delta > 0 else 0
                    mem_usage = stats["memory_stats"].get("usage", 0)
                    mem_limit = stats["memory_stats"].get("limit", 0)
                    return {
                        "pod": c.name, "container": c.name,
                        "cpu": f"{cpu_pct}%",
                        "memory": f"{round(mem_usage / 1024 / 1024, 1)}Mi" if mem_usage else "0",
                        "mem_limit": f"{round(mem_limit / 1024 / 1024 / 1024, 1)}Gi" if mem_limit else "",
                    }
                except Exception:
                    return None

            pod_metrics: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_get_container_stats, c) for c in running_containers]
                for f in as_completed(futures):
                    result = f.result()
                    if result:
                        pod_metrics.append(result)

            problems = [{"name": c.name, "phase": c.status, "reason": c.attrs.get("State", {}).get("Error", "")}
                        for c in containers if c.status not in ("running",)]
            return {
                "namespaces": [{"namespace": "compose", "pods": len(containers), "running": running,
                                "current": True, "problems": problems}],
                "services": services,
                "pod_metrics": pod_metrics,
            }
        except Exception as exc:
            return {"namespaces": [{"namespace": "compose", "current": True}], "error": str(exc)[:120]}

    def _get_jellyfin_libraries(self) -> dict[str, Any]:
        """Browse Jellyfin libraries."""
        api_keys = self._discover_api_keys()
        key = api_keys.get("jellyfin", "")
        if not key:
            return {"error": "no_key"}
        try:
            url = f"http://jellyfin:8096/Library/VirtualFolders"
            req = urllib.request.Request(url, headers={"X-Emby-Token": key})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            libraries = []
            for lib in (data if isinstance(data, list) else []):
                libraries.append({
                    "name": lib.get("Name", ""),
                    "type": lib.get("CollectionType", "unknown"),
                    "locations": lib.get("Locations", []),
                    "itemId": lib.get("ItemId", ""),
                })
            return {"libraries": libraries}
        except Exception as exc:
            return {"error": str(exc)[:80]}

    # --- New endpoints for round 4 ---

    def _get_service_logs(self, service_name: str, lines: int = 100) -> dict[str, Any]:
        """Get logs from a service container/pod."""
        import os
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                core_v1 = k8s_client.CoreV1Api()
                pods = core_v1.list_namespaced_pod(namespace, label_selector=f"app={service_name}")
                if not pods.items:
                    return {"service": service_name, "error": "no pods found"}
                pod_name = pods.items[0].metadata.name
                log_text = core_v1.read_namespaced_pod_log(
                    pod_name, namespace, tail_lines=lines, timestamps=True
                )
                return {"service": service_name, "pod": pod_name, "lines": log_text.splitlines()[-lines:]}
            except Exception as exc:
                return {"service": service_name, "error": str(exc)[:120]}
        else:
            try:
                import docker
                client = docker.from_env()
                container = client.containers.get(service_name)
                log_bytes = container.logs(tail=lines, timestamps=True)
                log_text = log_bytes.decode("utf-8", errors="replace")
                return {"service": service_name, "lines": log_text.splitlines()[-lines:]}
            except Exception as exc:
                return {"service": service_name, "error": str(exc)[:120]}

    def _check_image_updates(self) -> dict[str, Any]:
        """Compare running image digests for staleness detection."""
        import os
        namespace = os.environ.get("K8S_NAMESPACE", "")
        results: list[dict[str, str]] = []
        if namespace:
            try:
                from kubernetes import client as k8s_client, config as k8s_config
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
                apps_v1 = k8s_client.AppsV1Api()
                deps = apps_v1.list_namespaced_deployment(namespace)
                for dep in deps.items:
                    name = dep.metadata.name
                    if dep.spec.template.spec.containers:
                        c = dep.spec.template.spec.containers[0]
                        image = c.image or ""
                        # Extract tag/digest info
                        if "@sha256:" in image:
                            tag = "pinned (digest)"
                        elif ":" in image.split("/")[-1]:
                            tag = image.split(":")[-1]
                        else:
                            tag = "latest"
                        # Get deployment last-updated timestamp
                        last_updated = ""
                        if dep.metadata.creation_timestamp:
                            last_updated = dep.metadata.creation_timestamp.strftime("%Y-%m-%d %H:%M:%S")
                        for cond in (dep.status.conditions or []):
                            if cond.type == "Progressing" and cond.last_update_time:
                                last_updated = cond.last_update_time.strftime("%Y-%m-%d %H:%M:%S")
                        results.append({"name": name, "image": image, "tag": tag, "last_updated": last_updated})
            except Exception as exc:
                return {"error": str(exc)[:80]}
        else:
            try:
                import docker
                client = docker.from_env()
                for c in client.containers.list():
                    image = c.image.tags[0] if c.image.tags else str(c.image.short_id)
                    tag = image.split(":")[-1] if ":" in image else "latest"
                    # Get container start time and image creation time
                    started = c.attrs.get("State", {}).get("StartedAt", "")
                    created = c.image.attrs.get("Created", "") if c.image.attrs else ""
                    results.append({
                        "name": c.name, "image": image, "tag": tag,
                        "started_at": started[:19].replace("T", " ") if started else "",
                        "image_created": created[:19].replace("T", " ") if created else "",
                    })
            except Exception as exc:
                return {"error": str(exc)[:80]}
        pinned = sum(1 for r in results if r["tag"] not in ("latest",))
        return {"images": results, "total": len(results), "pinned": pinned}

    def _get_manifests(self) -> dict[str, Any]:
        """Return the compose file or kustomization content."""
        import os
        from pathlib import Path
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if namespace:
            # Try common kustomization paths
            for p in ["/opt/media-stack/k8s/kustomization.yaml", "/bootstrap-config/kustomization.yaml"]:
                path = Path(p)
                if path.exists():
                    return {"type": "kustomize", "file": str(path),
                            "content": path.read_text(encoding="utf-8", errors="replace")}
            return {"type": "kubernetes", "info": f"namespace={namespace}", "content": ""}
        else:
            compose_file = os.environ.get("COMPOSE_FILE", "")
            if compose_file and compose_file != "/dev/null":
                path = Path(compose_file)
                if path.exists():
                    return {"type": "compose", "file": str(path),
                            "content": path.read_text(encoding="utf-8", errors="replace")}
            # Try common paths
            for p in ["/opt/media-stack/docker/docker-compose.yml", "docker-compose.yml"]:
                path = Path(p)
                if path.exists():
                    return {"type": "compose", "file": str(path),
                            "content": path.read_text(encoding="utf-8", errors="replace")}
            return {"type": "compose", "content": "", "error": "compose file not found"}

    def _get_prometheus_metrics(self) -> str:
        """Generate Prometheus-format metrics."""
        lines: list[str] = []
        # Health metrics
        cached = _api_cache.get("health", 30)
        if cached:
            for svc, data in cached.get("services", {}).items():
                up = 1 if data.get("status") == "ok" else 0
                lines.append(f'media_stack_service_up{{service="{svc}"}} {up}')
                if "ms" in data:
                    lines.append(f'media_stack_service_response_ms{{service="{svc}"}} {data["ms"]}')
                auth = 1 if data.get("auth") == "ok" else 0
                lines.append(f'media_stack_service_auth_ok{{service="{svc}"}} {auth}')
            lines.append(f'media_stack_healthy_total {cached.get("healthy", 0)}')
            lines.append(f'media_stack_services_total {cached.get("total", 0)}')
        # State metrics
        state = self.state.to_dict()
        phase_val = {"idle": 0, "running": 1, "complete": 2, "error": 3}.get(state.get("phase", ""), -1)
        lines.append(f"media_stack_phase {phase_val}")
        lines.append(f'media_stack_bootstrap_done {1 if state.get("initial_bootstrap_done") else 0}')
        lines.append(f'media_stack_action_history_total {len(state.get("action_history", []))}')
        return "\n".join(lines) + "\n"

    def _rotate_keys(self) -> dict[str, Any]:
        """Regenerate API keys for all arr apps and update env/secrets."""
        import os
        import re
        import uuid
        from pathlib import Path

        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        rotated: dict[str, str] = {}
        errors: list[str] = []

        # Arr apps: regenerate ApiKey in config.xml
        arr_apps = ["sonarr", "radarr", "lidarr", "readarr", "prowlarr"]
        for app in arr_apps:
            cfg_path = Path(config_root) / app / "config.xml"
            if not cfg_path.is_file():
                continue
            try:
                content = cfg_path.read_text(encoding="utf-8")
                new_key = uuid.uuid4().hex
                content = re.sub(r"<ApiKey>[^<]*</ApiKey>", f"<ApiKey>{new_key}</ApiKey>", content)
                cfg_path.write_text(content, encoding="utf-8")
                env_key = f"{app.upper()}_API_KEY"
                os.environ[env_key] = new_key
                rotated[env_key] = new_key
            except Exception as exc:
                errors.append(f"{app}: {exc}")

        # Bazarr: regenerate apikey in config/config.yaml
        bazarr_cfg = Path(config_root) / "bazarr" / "config" / "config.yaml"
        if bazarr_cfg.is_file():
            try:
                import yaml
                with open(bazarr_cfg) as f:
                    bcfg = yaml.safe_load(f) or {}
                new_key = uuid.uuid4().hex
                bcfg.setdefault("auth", {})["apikey"] = new_key
                with open(bazarr_cfg, "w") as f:
                    yaml.dump(bcfg, f, default_flow_style=False)
                os.environ["BAZARR_API_KEY"] = new_key
                rotated["BAZARR_API_KEY"] = new_key
            except Exception as exc:
                errors.append(f"bazarr: {exc}")

        # SABnzbd: regenerate api_key in sabnzbd.ini
        sab_ini = Path(config_root) / "sabnzbd" / "sabnzbd.ini"
        if sab_ini.is_file():
            try:
                content = sab_ini.read_text(encoding="utf-8")
                new_key = uuid.uuid4().hex
                content = re.sub(r"^api_key\s*=\s*.*$", f"api_key = {new_key}", content, flags=re.MULTILINE)
                sab_ini.write_text(content, encoding="utf-8")
                os.environ["SABNZBD_API_KEY"] = new_key
                rotated["SABNZBD_API_KEY"] = new_key
            except Exception as exc:
                errors.append(f"sabnzbd: {exc}")

        # Persist to K8s secret if available
        self._persist_keys_to_secret(rotated)

        return {"status": "rotated", "keys": list(rotated.keys()), "errors": errors}

    def _reset_password(self, new_password: str) -> dict[str, Any]:
        """Reset admin password across all services."""
        import json as _json
        import os
        import re
        import urllib.request
        import http.cookiejar
        from pathlib import Path

        config_root = os.environ.get("CONFIG_ROOT", "/srv-config")
        old_password = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
        username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        updated: list[str] = []
        errors: list[str] = []

        # 1. qBittorrent — login with old password, set new via preferences API
        try:
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            login_data = f"username={username}&password={old_password}".encode()
            req = urllib.request.Request("http://qbittorrent:8080/api/v2/auth/login", data=login_data)
            opener.open(req, timeout=5)
            prefs = _json.dumps({"web_ui_password": new_password}).encode()
            req2 = urllib.request.Request(
                "http://qbittorrent:8080/api/v2/app/setPreferences",
                data=b"json=" + urllib.parse.quote(_json.dumps({"web_ui_password": new_password})).encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            opener.open(req2, timeout=5)
            updated.append("qbittorrent")
        except Exception as exc:
            errors.append(f"qbittorrent: {exc}")

        # 2. Jellyfin — authenticate, then change password via Users API
        try:
            jf_key = os.environ.get("JELLYFIN_API_KEY", "")
            jf_uid = os.environ.get("JELLYFIN_USER_ID", "")
            if jf_key and jf_uid:
                payload = _json.dumps({
                    "CurrentPw": old_password,
                    "NewPw": new_password,
                }).encode()
                req = urllib.request.Request(
                    f"http://jellyfin:8096/Users/{jf_uid}/Password",
                    data=payload,
                    method="POST",
                    headers={"X-Emby-Token": jf_key, "Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=10)
                updated.append("jellyfin")
            else:
                errors.append("jellyfin: JELLYFIN_API_KEY or JELLYFIN_USER_ID not set")
        except Exception as exc:
            errors.append(f"jellyfin: {exc}")

        # 3. Arr apps — update auth config via host config API
        arr_apps = [
            ("sonarr", 8989, "/api/v3/config/host", "SONARR_API_KEY"),
            ("radarr", 7878, "/api/v3/config/host", "RADARR_API_KEY"),
            ("lidarr", 8686, "/api/v1/config/host", "LIDARR_API_KEY"),
            ("readarr", 8787, "/api/v1/config/host", "READARR_API_KEY"),
            ("prowlarr", 9696, "/api/v1/config/host", "PROWLARR_API_KEY"),
        ]
        for app, port, api_path, key_env in arr_apps:
            try:
                api_key = os.environ.get(key_env, "")
                if not api_key:
                    api_key = self._read_xml_key(Path(config_root) / app / "config.xml")
                if not api_key:
                    errors.append(f"{app}: no API key available")
                    continue
                # GET current host config
                req = urllib.request.Request(
                    f"http://{app}:{port}{api_path}",
                    headers={"X-Api-Key": api_key, "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    cfg = _json.loads(resp.read())
                # Update password
                cfg["username"] = username
                cfg["password"] = new_password
                cfg["passwordConfirmation"] = new_password
                put_req = urllib.request.Request(
                    f"http://{app}:{port}{api_path}",
                    data=_json.dumps(cfg).encode(),
                    method="PUT",
                    headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
                )
                urllib.request.urlopen(put_req, timeout=5)
                updated.append(app)
            except Exception as exc:
                errors.append(f"{app}: {exc}")

        # 4. Bazarr — update auth in config.yaml
        try:
            bazarr_cfg = Path(config_root) / "bazarr" / "config" / "config.yaml"
            if bazarr_cfg.is_file():
                import yaml
                with open(bazarr_cfg) as f:
                    bcfg = yaml.safe_load(f) or {}
                bcfg.setdefault("auth", {})["username"] = username
                bcfg["auth"]["password"] = new_password
                bcfg["auth"]["type"] = "form"
                with open(bazarr_cfg, "w") as f:
                    yaml.dump(bcfg, f, default_flow_style=False)
                updated.append("bazarr")
        except Exception as exc:
            errors.append(f"bazarr: {exc}")

        # 5. Update env var for this controller process
        os.environ["STACK_ADMIN_PASSWORD"] = new_password

        # 5. Persist to K8s secret
        self._persist_keys_to_secret({
            "STACK_ADMIN_PASSWORD": new_password,
            "STACK_ADMIN_USERNAME": username,
        })

        # Note which services need restart (file-based config vs live API)
        restart_needed = [s for s in updated if s in ("bazarr",)]

        return {
            "status": "updated",
            "services": updated,
            "errors": errors,
            "restart_needed": restart_needed,
        }

    @staticmethod
    def _read_xml_key(path) -> str:
        """Read ApiKey from an arr app config.xml."""
        import re
        try:
            content = path.read_text(encoding="utf-8")
            m = re.search(r"<ApiKey>([^<]+)</ApiKey>", content)
            return m.group(1) if m else ""
        except Exception:
            return ""

    def _update_routing(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Update routing config in profile YAML and trigger regeneration."""
        import os
        from pathlib import Path

        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        resolved = _resolve_profile_path(profile_file)
        if not resolved:
            return {"error": "Profile file not found"}
        profile_path = Path(resolved)

        try:
            import yaml
            with open(profile_path) as f:
                profile = yaml.safe_load(f) or {}

            routing = profile.setdefault("routing", {})
            allowed_keys = {"base_domain", "stack_subdomain", "gateway_host", "gateway_port", "app_path_prefix", "strategy", "internet_exposed"}
            changed = []
            for key, value in updates.items():
                if key in allowed_keys and str(routing.get(key, "")) != str(value):
                    routing[key] = value
                    changed.append(key)

            # Auto-derive gateway_host if subdomain or domain changed
            if "stack_subdomain" in changed or "base_domain" in changed:
                sub = routing.get("stack_subdomain", "media-stack")
                dom = routing.get("base_domain", "local")
                routing["gateway_host"] = f"apps.{sub}.{dom}"
                if "gateway_host" not in changed:
                    changed.append("gateway_host")

            if not changed:
                return {"status": "no_changes", "routing": routing}

            with open(profile_path, "w") as f:
                yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

            # Queue envoy-config regeneration
            if self.action_trigger:
                self.action_trigger("envoy-config", {})

            return {"status": "updated", "changed": changed, "routing": routing}
        except Exception as exc:
            return {"error": str(exc)[:200]}

    def _persist_keys_to_secret(self, data: dict[str, str]) -> None:
        """Persist key-value pairs to K8s secret if available."""
        import os
        namespace = os.environ.get("K8S_NAMESPACE", "")
        if not namespace or not data:
            return
        try:
            from kubernetes import client as k8s_client, config as k8s_config
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()
            v1 = k8s_client.CoreV1Api()
            import base64
            secret_data = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
            try:
                existing = v1.read_namespaced_secret("media-stack-secrets", namespace)
                if existing.data:
                    existing.data.update(secret_data)
                else:
                    existing.data = secret_data
                v1.patch_namespaced_secret("media-stack-secrets", namespace, existing)
            except Exception:
                pass
        except Exception:
            pass

    def _batch_restart(self, service_names: list[str]) -> dict[str, Any]:
        """Restart multiple services."""
        results: dict[str, Any] = {}
        for name in service_names:
            if name in self._SERVICE_PROBES:
                results[name] = self._restart_service(name)
            else:
                results[name] = {"status": "error", "error": f"unknown service '{name}'"}
        ok = sum(1 for v in results.values() if v.get("status") == "restarted")
        return {"results": results, "restarted": ok, "total": len(service_names)}

    def _save_profile(self, content: str) -> dict[str, Any]:
        """Save bootstrap profile YAML."""
        import os
        from pathlib import Path
        profile_file = os.environ.get("BOOTSTRAP_PROFILE_FILE", "")
        resolved = _resolve_profile_path(profile_file)
        if not resolved:
            return {"error": "Profile file not found"}
        path = Path(resolved)
        try:
            path.write_text(content, encoding="utf-8")
            # Reload config
            if self.reload_config:
                self.reload_config()
            return {"status": "saved", "file": str(path)}
        except Exception as exc:
            return {"error": str(exc)[:120]}

    def _get_envvars(self) -> dict[str, str]:
        """Return relevant environment variables."""
        import os
        relevant_prefixes = ("BOOTSTRAP_", "STACK_", "CONFIG_", "MEDIA_", "K8S_",
                             "AUTO_", "COMPOSE_", "APP_", "ROUTE_", "ENVOY_")
        return {k: v for k, v in sorted(os.environ.items())
                if any(k.startswith(p) for p in relevant_prefixes)}

    def _set_envvar(self, key: str, value: str) -> dict[str, Any]:
        """Set an environment variable."""
        import os
        os.environ[key] = value
        return {"status": "set", "key": key, "value": value}

    def _get_rss_feed(self) -> str:
        """Generate RSS/Atom feed of action events and health changes."""
        state = self.state.to_dict()
        history = state.get("action_history", [])
        items = []
        for a in reversed(history[-20:]):
            status = "error" if a.get("error") else "complete"
            title = f"Action: {a.get('name', '?')} — {status}"
            desc = f"Duration: {a.get('elapsed_seconds', '?')}s"
            if a.get("error"):
                desc += f"\nError: {a['error']}"
            items.append(f"""  <item>
    <title>{title}</title>
    <description><![CDATA[{desc}]]></description>
    <category>{status}</category>
  </item>""")
        # Add current health summary as an item
        cached = _api_cache.get("health", 60)
        if cached:
            healthy = cached.get("healthy", 0)
            total = cached.get("total", 0)
            items.insert(0, f"""  <item>
    <title>Health: {healthy}/{total} services up</title>
    <description><![CDATA[Last probe results]]></description>
    <category>health</category>
  </item>""")
        channel_items = "\n".join(items)
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Media Stack Controller</title>
  <description>Action events and health status</description>
  <link>/</link>
  <lastBuildDate>{time.strftime("%a, %d %b %Y %H:%M:%S %z")}</lastBuildDate>
{channel_items}
</channel>
</rss>"""

    def _get_grafana_dashboard(self) -> dict[str, Any]:
        """Generate a Grafana dashboard JSON that queries /metrics."""
        panels = []
        y = 0
        # Service up/down panel
        panels.append({
            "type": "stat", "title": "Services Up", "gridPos": {"h": 4, "w": 6, "x": 0, "y": y},
            "targets": [{"expr": "media_stack_healthy_total", "legendFormat": "Healthy"}],
            "fieldConfig": {"defaults": {"thresholds": {"steps": [{"color": "red", "value": 0}, {"color": "green", "value": 14}]}}},
        })
        panels.append({
            "type": "stat", "title": "Total Services", "gridPos": {"h": 4, "w": 6, "x": 6, "y": y},
            "targets": [{"expr": "media_stack_services_total", "legendFormat": "Total"}],
        })
        panels.append({
            "type": "stat", "title": "Bootstrap Done", "gridPos": {"h": 4, "w": 6, "x": 12, "y": y},
            "targets": [{"expr": "media_stack_bootstrap_done", "legendFormat": "Done"}],
            "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "No"}, "1": {"text": "Yes"}}}]}},
        })
        panels.append({
            "type": "stat", "title": "Phase", "gridPos": {"h": 4, "w": 6, "x": 18, "y": y},
            "targets": [{"expr": "media_stack_phase", "legendFormat": "Phase"}],
            "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {"0": {"text": "Idle"}, "1": {"text": "Running"}, "2": {"text": "Complete"}, "3": {"text": "Error"}}}]}},
        })
        y += 4
        # Per-service response time
        panels.append({
            "type": "timeseries", "title": "Service Response Time (ms)", "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
            "targets": [{"expr": "media_stack_service_response_ms", "legendFormat": "{{service}}"}],
            "fieldConfig": {"defaults": {"unit": "ms"}},
        })
        y += 8
        # Per-service up/down
        panels.append({
            "type": "timeseries", "title": "Service Availability", "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
            "targets": [{"expr": "media_stack_service_up", "legendFormat": "{{service}}"}],
        })
        return {
            "__inputs": [{"name": "DS_PROMETHEUS", "type": "datasource", "pluginId": "prometheus"}],
            "title": "Media Stack Controller",
            "uid": "media-stack-controller",
            "version": 1,
            "time": {"from": "now-1h", "to": "now"},
            "refresh": "30s",
            "panels": panels,
            "templating": {"list": []},
            "schemaVersion": 39,
        }

    def _get_openapi_spec(self) -> dict[str, Any]:
        """Generate OpenAPI 3.0 spec from registered endpoints."""
        paths: dict[str, Any] = {}
        get_endpoints = [
            ("/healthz", "Liveness probe", {"status": "ok"}),
            ("/readyz", "Readiness probe", {"status": "ready", "initial_bootstrap_done": True, "phase": "complete"}),
            ("/status", "Full controller state", {}),
            ("/apps", "All app statuses", {}),
            ("/apps/{name}", "Single app status", {}),
            ("/config", "Runtime config", {}),
            ("/webhooks", "List webhook URLs", {}),
            ("/api/health", "Live service health probes with auth validation", {}),
            ("/api/versions", "Service version strings", {}),
            ("/api/downloads", "Active download queues from qBittorrent/SABnzbd", {}),
            ("/api/stats", "Library counts from arr apps", {}),
            ("/api/indexers", "Prowlarr indexer list", {}),
            ("/api/indexer-stats", "Prowlarr indexer performance stats", {}),
            ("/api/disk", "Disk usage on media/config volumes", {}),
            ("/api/env", "Runtime environment info, ingress hosts, node IP", {}),
            ("/api/recent", "Recently added media from arr apps", {}),
            ("/api/profile", "Bootstrap profile YAML", {}),
            ("/api/health-history", "Persistent health history with SLA percentages", {}),
            ("/api/logs/{service}", "Container/pod logs for a service (query: ?lines=N)", {}),
            ("/api/image-updates", "Running image versions and tag info", {}),
            ("/api/manifests", "Docker Compose or Kustomize manifest content", {}),
            ("/api/envvars", "Relevant environment variables", {}),
            ("/api/envoy/stats", "Envoy proxy traffic statistics", {}),
            ("/api/download-history", "Download history from arr apps", {}),
            ("/api/quality-profiles", "Quality profiles from arr apps", {}),
            ("/api/import-lists", "Import list status from arr apps", {}),
            ("/api/namespaces", "K8s namespaces or Compose containers with resource metrics", {}),
            ("/api/libraries", "Jellyfin library listing", {}),
            ("/api/backup", "Download full config backup as JSON", {}),
            ("/api/feed.xml", "RSS feed of action events and health status", {}),
            ("/api/grafana.json", "Grafana dashboard JSON for Prometheus", {}),
            ("/api/openapi.json", "This OpenAPI specification", {}),
            ("/metrics", "Prometheus metrics endpoint", {}),
        ]
        for path, desc, example in get_endpoints:
            paths[path] = {"get": {
                "summary": desc,
                "responses": {"200": {"description": "Success",
                    "content": {"application/json": {"example": example}} if not path.endswith((".xml", ".json")) or path == "/api/openapi.json" else {}}},
            }}
        post_endpoints = [
            ("/actions/{name}", "Trigger an action (bootstrap, auto-indexers, reconcile, etc.)"),
            ("/config", "Update runtime config"),
            ("/webhooks", "Register a webhook URL"),
            ("/webhooks/test", "Send test payload to all webhooks"),
            ("/api/restart/{service}", "Restart a single service"),
            ("/api/batch-restart", "Restart multiple services"),
            ("/api/profile", "Save bootstrap profile YAML"),
            ("/api/envvars", "Set an environment variable"),
            ("/reload", "Reload bootstrap profile"),
            ("/reset", "Reset controller state"),
            ("/cancel", "Cancel running action"),
        ]
        for path, desc in post_endpoints:
            if path not in paths:
                paths[path] = {}
            paths[path]["post"] = {"summary": desc, "responses": {"200": {"description": "Success"}}}
        return {
            "openapi": "3.0.3",
            "info": {"title": "Media Stack Controller API", "version": "1.0.0",
                     "description": "API for managing the media automation stack"},
            "paths": paths,
        }

    def _load_plugins(self) -> str:
        """Load custom JS/CSS from plugin mount directory."""
        import os
        from pathlib import Path
        plugin_dir = Path(os.environ.get("PLUGIN_DIR", "/srv-config/controller-plugins"))
        if not plugin_dir.exists():
            return ""
        parts: list[str] = []
        # Load CSS files
        for css in sorted(plugin_dir.glob("*.css")):
            try:
                parts.append(f"<style>/* plugin: {css.name} */\n{css.read_text(encoding='utf-8')}</style>")
            except Exception:
                pass
        # Load JS files
        for js in sorted(plugin_dir.glob("*.js")):
            try:
                parts.append(f"<script>/* plugin: {js.name} */\n{js.read_text(encoding='utf-8')}</script>")
            except Exception:
                pass
        return "\n".join(parts)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]  # strip query string for routing

        if path == "/healthz":
            self._json_response(200, {"status": "ok"})
        elif path == "/readyz":
            self._json_response(200, {
                "status": "ready",
                "initial_bootstrap_done": self.state.initial_bootstrap_done,
                "phase": self.state.phase,
            })
        elif path == "/status":
            self._json_response(200, self.state.to_dict())
        elif path == "/apps":
            self._json_response(200, {"apps": dict(self.state.app_status)})
        elif path.startswith("/apps/") and path.count("/") == 2:
            app_name = path.split("/")[2]
            info = self.state.app_status.get(app_name)
            if info:
                self._json_response(200, {app_name: info})
            else:
                self._json_response(404, {"error": f"app '{app_name}' not found"})
        elif path == "/config":
            self._json_response(200, {"config": dict(self.state.runtime_config)})
        elif path == "/webhooks":
            self._json_response(200, {"webhook_urls": list(self.state.webhook_urls)})
        elif path == "/logs/stream":
            self._sse_response()
        elif path == "/api/health":
            self._json_response(200, self._probe_services())
        elif path == "/api/versions":
            self._json_response(200, self._get_versions())
        elif path == "/api/downloads":
            self._json_response(200, self._get_downloads())
        elif path == "/api/stats":
            self._json_response(200, self._get_stats())
        elif path == "/api/indexers":
            self._json_response(200, self._get_indexers())
        elif path == "/api/disk":
            self._json_response(200, self._get_disk())
        elif path == "/api/env":
            self._json_response(200, self._get_env())
        elif path == "/api/routing":
            self._json_response(200, self._get_routing())
        elif path == "/api/recent":
            self._json_response(200, self._get_recent())
        elif path == "/api/profile":
            self._json_response(200, self._get_profile())
        elif path == "/api/envoy/stats":
            self._json_response(200, self._get_envoy_stats())
        elif path == "/api/download-history":
            self._json_response(200, self._get_download_history())
        elif path == "/api/indexer-stats":
            self._json_response(200, self._get_indexer_stats())
        elif path == "/api/quality-profiles":
            self._json_response(200, self._get_quality_profiles())
        elif path == "/api/import-lists":
            self._json_response(200, self._get_import_lists())
        elif path == "/api/namespaces":
            self._json_response(200, self._get_namespaces())
        elif path == "/api/libraries":
            self._json_response(200, self._get_jellyfin_libraries())
        elif path == "/api/backup":
            payload = self._get_backup()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f'attachment; filename="media-stack-backup-{time.strftime("%Y%m%d-%H%M%S")}.json"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/health-history":
            self._json_response(200, self._get_health_history())
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
            self._json_response(200, self._get_service_logs(svc, lines))
        elif path == "/api/image-updates":
            self._json_response(200, self._check_image_updates())
        elif path == "/api/manifests":
            self._json_response(200, self._get_manifests())
        elif path == "/api/envvars":
            self._json_response(200, self._get_envvars())
        elif path == "/metrics":
            payload = self._get_prometheus_metrics().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/feed.xml":
            payload = self._get_rss_feed().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/grafana.json":
            self._json_response(200, self._get_grafana_dashboard())
        elif path == "/api/openapi.json":
            self._json_response(200, self._get_openapi_spec())
        elif path == "/" or path == "/dashboard":
            html = _DASHBOARD_HTML
            # Inject plugins before </body>
            plugins = self._load_plugins()
            if plugins:
                html = html.replace("</body>", plugins + "\n</body>")
            self._html_response(200, html)
        elif path == "/api/docs":
            self._html_response(200, _API_DOCS_HTML)
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        # POST /run — backward-compatible alias for /actions/bootstrap
        if self.path == "/run":
            self._handle_action("bootstrap")
            return

        # POST /api/restart/{service}
        if self.path.startswith("/api/restart/"):
            service_name = self.path[len("/api/restart/"):]
            valid_services = set(self._SERVICE_PROBES.keys())
            if service_name not in valid_services:
                self._json_response(400, {"error": f"unknown service '{service_name}'", "valid": sorted(valid_services)})
                return
            result = self._restart_service(service_name)
            status_code = 200 if result.get("status") == "restarted" else 500
            self._json_response(status_code, result)
            return

        # POST /api/batch-restart — restart multiple services
        if self.path == "/api/batch-restart":
            body = self._read_json_body()
            services = body.get("services", [])
            if not services:
                self._json_response(400, {"error": "services list required"})
                return
            self._json_response(200, self._batch_restart(services))
            return

        # POST /api/profile — save bootstrap profile
        if self.path == "/api/profile":
            body = self._read_json_body()
            content = body.get("content", "")
            if not content:
                self._json_response(400, {"error": "content field required"})
                return
            self._json_response(200, self._save_profile(content))
            return

        # POST /api/envvars — set environment variable
        if self.path == "/api/envvars":
            body = self._read_json_body()
            key = body.get("key", "")
            value = body.get("value", "")
            if not key:
                self._json_response(400, {"error": "key field required"})
                return
            self._json_response(200, self._set_envvar(key, value))
            return

        # POST /api/rotate-keys — regenerate API keys for all services
        if self.path == "/api/rotate-keys":
            self._json_response(200, self._rotate_keys())
            return

        # POST /api/reset-password — reset admin password across all services
        if self.path == "/api/reset-password":
            body = self._read_json_body()
            new_password = body.get("password", "")
            if not new_password or len(new_password) < 4:
                self._json_response(400, {"error": "password field required (min 4 chars)"})
                return
            self._json_response(200, self._reset_password(new_password))
            return

        # POST /api/routing — update routing config and regenerate envoy
        if self.path == "/api/routing":
            body = self._read_json_body()
            if not body:
                self._json_response(400, {"error": "JSON body required"})
                return
            self._json_response(200, self._update_routing(body))
            return

        # POST /webhooks/test
        if self.path == "/webhooks/test":
            self._json_response(200, self._test_webhook())
            return

        # POST /actions/{name}
        if self.path.startswith("/actions/"):
            action_name = self.path[len("/actions/"):]
            if action_name not in KNOWN_ACTIONS:
                self._json_response(
                    404, {"error": f"unknown action '{action_name}'", "known": sorted(KNOWN_ACTIONS)}
                )
                return
            self._handle_action(action_name)
            return

        # POST /config — update runtime config toggles
        if self.path == "/config":
            body = self._read_json_body()
            if not body:
                self._json_response(400, {"error": "JSON body required"})
                return
            updated = self.state.update_config(body)
            logger.info("Config updated: %s", body)
            self._json_response(200, {"status": "updated", "config": updated})
            return

        # POST /webhooks — register a webhook URL
        if self.path == "/webhooks":
            body = self._read_json_body()
            url = str(body.get("url", "")).strip()
            if not url:
                self._json_response(400, {"error": "url field required"})
                return
            if url not in self.state.webhook_urls:
                self.state.webhook_urls.append(url)
            logger.info("Webhook registered: %s", url)
            self._json_response(200, {"status": "registered", "webhook_urls": list(self.state.webhook_urls)})
            return

        # POST /reload — reload profile and apply config policy
        if self.path == "/reload":
            if self.reload_config is not None:
                try:
                    self.reload_config()
                    self._json_response(200, {"status": "reloaded"})
                except Exception as exc:
                    self._json_response(500, {"error": f"reload failed: {exc}"})
            else:
                self._json_response(503, {"error": "no reload handler configured"})
            return

        # POST /reset — reset state for re-run
        if self.path == "/reset":
            if self.state.action_running:
                self._json_response(409, {"error": "cannot reset while action is running"})
            else:
                logger.info("State reset requested")
                self.state.phase = "idle"
                self.state.error = None
                self._json_response(200, {"status": "reset"})
            return

        # POST /cancel — cancel the current action
        if self.path == "/cancel":
            if self.state.cancel_action():
                self._json_response(
                    200,
                    {
                        "status": "cancel_requested",
                        "action": self.state.current_action.name
                        if self.state.current_action
                        else None,
                    },
                )
            else:
                self._json_response(409, {"error": "no action running to cancel"})
            return

        self._json_response(404, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        # DELETE /actions/{id} — cancel a specific action by id
        if self.path.startswith("/actions/"):
            action_id = self.path[len("/actions/"):]
            action = self.state.get_action(action_id)
            if not action:
                self._json_response(404, {"error": f"action '{action_id}' not found"})
            elif action.is_terminal:
                self._json_response(409, {"error": "action already completed", "status": action.status.value})
            else:
                self.state.cancel_action()
                self._json_response(200, {"status": "cancel_requested", "action": action.to_dict()})
            return
        # DELETE /webhooks — remove a webhook URL (pass {"url": "..."} in body)
        if self.path == "/webhooks":
            body = self._read_json_body()
            url = str(body.get("url", "")).strip()
            if url in self.state.webhook_urls:
                self.state.webhook_urls.remove(url)
                self._json_response(200, {"status": "removed", "webhook_urls": list(self.state.webhook_urls)})
            else:
                self._json_response(404, {"error": "webhook URL not found"})
            return
        self._json_response(404, {"error": "not found"})

    def _handle_action(self, action_name: str) -> None:
        if self.state.action_running:
            current = self.state.current_action
            logger.warning("Action rejected: %s in progress", current.name if current else "unknown")
            self._json_response(
                409,
                {
                    "error": "action already in progress",
                    "current_action": current.to_dict() if current else None,
                },
            )
            return
        if self.action_trigger is None:
            self._json_response(503, {"error": "no action trigger configured"})
            return
        overrides = self._read_json_body()
        # Merge runtime_config as defaults (explicit overrides take precedence).
        merged = {**self.state.runtime_config, **overrides}
        logger.info("Action accepted: %s (overrides=%s)", action_name, merged)
        self.action_trigger(action_name, merged)
        self._json_response(202, {"status": "accepted", "action": action_name, "overrides": merged})


ReloadConfigFn = Callable[[], None]


def start_api_server(
    state: BootstrapState,
    *,
    port: int = 9100,
    action_trigger: ActionTriggerFn | None = None,
    reload_config: ReloadConfigFn | None = None,
) -> ThreadingHTTPServer:
    """Start the bootstrap API HTTP server on a daemon thread."""

    attrs: dict[str, Any] = {"state": state}
    if action_trigger:
        attrs["action_trigger"] = staticmethod(action_trigger)
    if reload_config:
        attrs["reload_config"] = staticmethod(reload_config)
    handler_class = type("BoundHandler", (BootstrapAPIHandler,), attrs)

    server = ThreadingHTTPServer(("0.0.0.0", port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

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
