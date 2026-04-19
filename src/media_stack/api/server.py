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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .state import ControllerState
from . import handlers_get
from . import handlers_post

try:
    from media_stack.core.auth.users.user_service_factory import (
        build_default_auth_verifier as _build_auth_verifier,
        build_default_scheduled_reconciler as _build_sched_reconciler,
        build_default_api_token_store as _build_token_store,
        build_default_service as _build_user_service,
    )
except ImportError:
    _build_auth_verifier = None
    _build_sched_reconciler = None
    _build_token_store = None
    _build_user_service = None

from media_stack.core.auth.failed_login_tracker import FailedLoginTracker

# Per-IP lockout: after _IP_LOCKOUT_THRESHOLD failures within
# _IP_LOCKOUT_WINDOW seconds, the IP is rejected outright for
# _IP_LOCKOUT_COOLDOWN. Keyed by source IP, so credential-stuffing
# across many usernames still trips the lock. Independent from the
# per-account tracker used by BasicAuthVerifier.
_IP_LOCKOUT_THRESHOLD = 20
_IP_LOCKOUT_WINDOW = 5 * 60
_IP_LOCKOUT_COOLDOWN = 15 * 60
_ip_failure_tracker = FailedLoginTracker(
    threshold=_IP_LOCKOUT_THRESHOLD,
    window_seconds=_IP_LOCKOUT_WINDOW,
    cooldown_seconds=_IP_LOCKOUT_COOLDOWN,
)


_H_CONTENT_LENGTH = "Content-Length"
# Cap request body at 1 MiB. Bulk CSV imports and config uploads stay
# well under this; anything larger is either a mistake or an attack.
_MAX_BODY_BYTES = 1 * (2 ** 20)

# Forward-auth integration. When Envoy fronts the controller with an
# Authelia ext_authz filter, Authelia sets Remote-User (and friends)
# on the upstream request. The controller accepts that identity only
# if the request came from a CIDR listed in CONTROLLER_TRUSTED_PROXY_CIDRS.
# Without a trusted-proxy configuration, these headers are ignored so
# an attacker can't spoof Remote-User by setting the header themselves.
_DEFAULT_TRUSTED_PROXY_HEADER = "Remote-User"


class _AuthPolicy:
    """Encapsulates the auth decision + bearer-token verification.

    Extracted out of ControllerAPIHandler so that class stays under the
    class-method ratchet. Instance methods take the live handler so they
    can read headers/command/path without ControllerAPIHandler having to
    carry the logic."""

    _PUBLIC_PATHS = frozenset({"/healthz", "/readyz", "/webhooks/arr"})

    def is_public(self, handler, path: str) -> bool:
        if path in self._PUBLIC_PATHS:
            return True
        if path == "/api/invites/accept" and handler.command == "POST":
            return True
        return False

    def decision(self, handler, path: str, password: str) -> str:
        auth_mode = os.environ.get("CONTROLLER_AUTH", "").strip().lower()
        if not auth_mode:
            auth_mode = "all" if password else "none"
        if auth_mode == "none":
            return "allow"
        is_sensitive = (
            path.startswith("/api/")
            or path == "/metrics"
            or path.startswith("/logs/")
        )
        if auth_mode == "write" and handler.command == "GET" and not is_sensitive:
            return "allow"
        return "require"

    def verify_bearer(self, handler, plaintext: str) -> bool:
        if _build_token_store is None or not plaintext:
            return False
        try:
            tok = _build_token_store().verify(plaintext)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] token verify failed: %s", exc,
            )
            return False
        if tok is None:
            return False
        if tok.scope == "read" and handler.command != "GET":
            return False
        return True

    def send_401(self, handler) -> None:
        handler.send_response(401)
        handler.send_header(
            "WWW-Authenticate", 'Basic realm="Media Stack Controller"',
        )
        handler.send_header(_H_CONTENT_LENGTH, "0")
        handler.end_headers()

    def canonicalize_path(self, handler) -> None:
        raw = getattr(handler, "path", None)
        if not isinstance(raw, str):
            return
        qmark = raw.find("?")
        path = raw if qmark < 0 else raw[:qmark]
        query = "" if qmark < 0 else raw[qmark:]
        if (path.startswith("/api/") and len(path) > len("/api/")
                and path.endswith("/")):
            handler.path = path.rstrip("/") + query

    def check_body_size(self, handler) -> bool:
        headers = getattr(handler, "headers", None)
        if headers is None:
            return True  # tests with mocked handlers skip the cap
        try:
            raw = headers.get(_H_CONTENT_LENGTH, "") or ""
        except AttributeError:
            return True
        try:
            length = int(raw) if raw else 0
        except ValueError:
            length = 0
        if length <= _MAX_BODY_BYTES:
            return True
        body = json.dumps({
            "error": f"request body too large ({length} bytes); "
                     f"max {_MAX_BODY_BYTES}",
        }).encode()
        handler.send_response(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        handler.send_header("Content-Type", "application/json")
        handler.send_header(_H_CONTENT_LENGTH, str(len(body)))
        self.emit_security_headers(handler)
        handler.end_headers()
        handler.wfile.write(body)
        return False

    def emit_security_headers(self, handler) -> None:
        """Send hardening headers on every response.

        CSP uses 'unsafe-inline' because dashboard.html has inline
        scripts/styles; tightening to nonce-based CSP is the ideal
        next step.
        """
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("X-Frame-Options", "DENY")
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("Permissions-Policy",
                           "geolocation=(), camera=(), microphone=()")
        handler.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        handler.send_header("Strict-Transport-Security",
                           "max-age=31536000; includeSubDomains")


_auth_policy = _AuthPolicy()


class _TrustedProxyAuth:
    """Accept an upstream-auth'd identity (e.g. from Authelia via
    Envoy ext_authz) only when the request arrives from a configured
    trusted proxy CIDR.

    Env vars:
      CONTROLLER_TRUSTED_PROXY_CIDRS  — comma-separated CIDRs (v4 or v6)
                                        of the proxy tier (Envoy pod IPs,
                                        Tailscale net, etc.). Empty =
                                        trusted-proxy auth disabled.
      CONTROLLER_TRUSTED_PROXY_HEADER — header that carries the identity.
                                        Defaults to ``Remote-User``.

    An identity-carrying header coming from an IP OUTSIDE the trusted
    CIDR list is IGNORED — we never blindly trust user-supplied
    Remote-User headers.
    """

    def __init__(self) -> None:
        # Bind the env dict once so each identity() call reads via
        # self._env.get(...) rather than os.environ.get(...). Same dict
        # object — test mock.patch.dict(os.environ) still takes effect.
        self._env = os.environ

    def identity(self, handler) -> str | None:
        cidrs_raw = self._env.get("CONTROLLER_TRUSTED_PROXY_CIDRS", "").strip()
        if not cidrs_raw:
            return None  # trusted-proxy auth not configured
        header = (self._env.get("CONTROLLER_TRUSTED_PROXY_HEADER", "")
                  .strip() or _DEFAULT_TRUSTED_PROXY_HEADER)
        client_ip = self._client_ip(handler)
        if not client_ip or not self._in_any_cidr(client_ip, cidrs_raw):
            return None
        headers = getattr(handler, "headers", None)
        if headers is None:
            return None
        try:
            user = (headers.get(header, "") or "").strip()
        except AttributeError:
            return None
        return user or None

    def _client_ip(self, handler) -> str:
        addr = getattr(handler, "client_address", None)
        if isinstance(addr, tuple) and addr:
            return str(addr[0])
        return ""

    def _in_any_cidr(self, ip_str: str, cidrs_raw: str) -> bool:
        import ipaddress
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        for chunk in cidrs_raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                net = ipaddress.ip_network(chunk, strict=False)
            except ValueError:
                continue
            if ip in net:
                return True
        return False


_trusted_proxy_auth = _TrustedProxyAuth()


def _audit_actor_from(handler) -> str:
    """Best-effort actor identity for audit entries.

    Tries (in order):
      - Authelia Remote-User forwarded by a trusted proxy
      - The username half of a Basic auth header
      - 'bearer-token' when a Bearer header was presented
      - 'anonymous' as the final fallback
    """
    try:
        remote = _trusted_proxy_auth.identity(handler)
        if remote:
            return remote
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("media_stack").debug(
            "[DEBUG] _audit_actor_from trusted_proxy_auth raised: %s", exc,
        )
    auth = (handler.headers.get("Authorization", "") if
            getattr(handler, "headers", None) else "") or ""
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8", "replace")
            return decoded.partition(":")[0] or "anonymous"
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] _audit_actor_from Basic decode failed: %s", exc,
            )
            return "anonymous"
    if auth.startswith("Bearer "):
        return "bearer-token"
    return "anonymous"


# Paths whose POST traffic we audit. GET is not audited (reads don't
# change state); the user-mgmt service has its own finer-grained audit
# that runs in addition to this so detail-rich entries still happen.
_AUDIT_SKIP_POST_PATHS = frozenset({
    "/healthz", "/readyz", "/webhooks/arr",
})


def _audit_mutation(handler) -> None:
    """Emit an audit-log entry for a 2xx mutating POST.

    Runs AFTER the handler returns so the logged result includes the
    status the business logic chose (400/404/500 mutations aren't
    audited as successful changes). Falls back silently on any error
    so a misbehaving audit path can't block a real request.
    """
    if _build_user_service is None:
        return
    try:
        status = int(getattr(handler, "_last_status", 0))
        if not (HTTPStatus.OK <= status < HTTPStatus.MULTIPLE_CHOICES):
            return
        path = (getattr(handler, "path", "") or "").split("?")[0]
        if path in _AUDIT_SKIP_POST_PATHS:
            return
        # User-mgmt endpoints write their own audit entries with more
        # detail; skip here to avoid duplicate rows.
        if (path == "/api/users" or path.startswith("/api/users/")
                or path.startswith("/api/invites")
                or path.startswith("/api/roles/")
                or path == "/api/users-bulk-import"
                or path == "/api/users-reconcile/import"
                or path == "/api/users-reconcile/unlink"):
            return
        svc = _build_user_service()
        svc._audit.append(
            actor=_audit_actor_from(handler),
            action="api_mutation",
            target=path,
            result="ok",
            detail={
                "method": getattr(handler, "command", "POST"),
                "status": status,
                "client": _trusted_proxy_auth._client_ip(handler),
            },
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("media_stack").debug(
            "[DEBUG] _audit_mutation failed: %s", exc,
        )


def _verify_basic_auth(auth_header: str, fb_user: str, fb_pass: str) -> bool:
    """Verify basic-auth. Prefer the store-backed verifier so password
    resets in the UI take effect immediately; fall back to env creds.
    """
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("media_stack").debug("[DEBUG] bad auth header: %s", exc)
        return False
    provided_user, _, provided_pass = decoded.partition(":")
    if _build_auth_verifier is not None:
        try:
            if _build_auth_verifier().verify(provided_user, provided_pass):
                return True
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] store-backed verifier failed: %s", exc,
            )
    return provided_user == fb_user and provided_pass == fb_pass

# Re-export for backward compatibility — other modules import these from server.py
from .webhooks import _fire_webhooks  # noqa: F401
from .cache import api_cache as _api_cache  # noqa: F401
from .handlers_get import _build_openapi_servers  # noqa: F401

logger = logging.getLogger("controller_api")

ActionTriggerFn = Callable[[str, dict[str, Any]], None]


# Re-export constants that other modules import from server.py
KNOWN_ACTIONS = handlers_post.KNOWN_ACTIONS

# Lower number = higher priority. Used by PriorityQueue in the dispatch loop.
# Core action priorities (configure-* jobs get priority from their contract).
_CORE_ACTION_PRIORITY: dict[str, int] = {
    "bootstrap":     10,
    "configure-media-server": 15,
    "validate-credentials": 20,
    "envoy-config":  30,
    "restart-apps":  40,
    "post-setup":      45,
    "reconcile":     50,
    "push-indexers": 60,
    "discover-indexers": 70,
}

def _build_action_priority() -> dict[str, int]:
    """Build ACTION_PRIORITY from core + contract-discovered jobs."""
    priorities = dict(_CORE_ACTION_PRIORITY)
    try:
        from media_stack.cli.commands.job_framework import discover_jobs_from_contracts
        _PHASE_BASE = {"media_server": 40, "download_clients": 50, "default": 55, "post": 75}
        for job in discover_jobs_from_contracts():
            base = _PHASE_BASE.get(job["phase"], 55)
            priorities.setdefault(job["name"], base + job.get("priority", 50) // 10)
    except Exception as exc:
        logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
        pass
    return priorities

ACTION_PRIORITY: dict[str, int] = _build_action_priority()
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
        """Check authentication (trusted proxy, bearer token, or basic auth).

        Order:
          0. IP-lockout check — an IP that's burned through its failure
             budget is rejected 429 immediately, no auth path tried.
          1. Trusted-proxy — Remote-User from an allowlisted CIDR wins.
          2. Bearer token — programmatic clients.
          3. Basic auth — browser + curl.

        Auth is skipped for public endpoints and when CONTROLLER_AUTH
        resolves to ``none``.
        """
        path = self.path.split("?")[0]
        if _auth_policy.is_public(self, path):
            return True
        username = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        password = os.environ.get("STACK_ADMIN_PASSWORD", "")
        if _auth_policy.decision(self, path, password) == "allow":
            return True
        client_ip = _trusted_proxy_auth._client_ip(self)
        if client_ip and _ip_failure_tracker.is_locked(client_ip):
            self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
            self.send_header("Content-Type", "application/json")
            self.send_header(_H_CONTENT_LENGTH, "0")
            self.end_headers()
            return False
        if _trusted_proxy_auth.identity(self):
            return True
        if not password:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            if _auth_policy.verify_bearer(
                self, auth_header[len("Bearer "):].strip(),
            ):
                return True
        elif _verify_basic_auth(auth_header, username, password):
            return True
        if client_ip:
            _ip_failure_tracker.register_failure(client_ip)
        _auth_policy.send_401(self)
        return False

    # --- Response helpers ---

    def _json_response(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, default=str).encode("utf-8")
        self._last_status = int(status)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header(_H_CONTENT_LENGTH, str(len(payload)))
        _auth_policy.emit_security_headers(self)
        self.end_headers()
        self.wfile.write(payload)

    def _html_response(self, status: int, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header(_H_CONTENT_LENGTH, str(len(payload)))
        _auth_policy.emit_security_headers(self)
        self.end_headers()
        self.wfile.write(payload)

    def _raw_response(self, status: int, content_type: str, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header(_H_CONTENT_LENGTH, str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        _auth_policy.emit_security_headers(self)
        self.end_headers()
        self.wfile.write(payload)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get(_H_CONTENT_LENGTH, 0))
        if length <= 0:
            return {}
        # Defensive: clamp to the cap even if do_POST preflight was bypassed.
        length = min(length, _MAX_BODY_BYTES)
        try:
            return json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Malformed JSON body on %s %s: %s", self.command, self.path, exc)
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
        _auth_policy.canonicalize_path(self)
        if not self._check_auth():
            return
        handlers_get.handle(self)

    # =======================================================================
    # POST routing — delegates to handlers_post
    # =======================================================================

    def do_POST(self) -> None:  # noqa: N802
        _auth_policy.canonicalize_path(self)
        if not self._check_auth():
            return
        if not _auth_policy.check_body_size(self):
            return
        handlers_post.handle(self)
        _audit_mutation(self)


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

    if _build_sched_reconciler is not None:
        try:
            _build_sched_reconciler().start()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("media_stack").debug(
                "[DEBUG] scheduled reconcile not started: %s", exc,
            )

    # Graceful shutdown on SIGTERM
    def _shutdown(signum: int, frame: Any) -> None:
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    return server
