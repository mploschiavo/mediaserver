"""POST route handlers — extracted from ControllerAPIHandler.do_POST().

Every public function receives the ControllerAPIHandler instance as its
first argument so it can call response helpers and access ``self.state``.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from .services import admin as admin_svc
from .services import config as config_svc
from .services import disk as disk_svc
from .services import health as health_svc
from .services import ops as ops_svc

if TYPE_CHECKING:
    from .server import ControllerAPIHandler

logger = logging.getLogger("controller_api")


# ---------------------------------------------------------------------------
# Known actions
# ---------------------------------------------------------------------------

KNOWN_ACTIONS = frozenset({
    "bootstrap", "finalize", "auto-indexers", "restart-apps",
    "sync-indexers", "envoy-config", "reconcile", "validate-credentials",
})


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def handle(handler: ControllerAPIHandler) -> None:  # noqa: C901
    """Route a POST request to the appropriate handler function."""

    # POST /run -- backward-compatible alias
    if handler.path == "/run":
        handler._handle_action("bootstrap")
        return

    # POST /api/restart/{service}
    if handler.path.startswith("/api/restart/"):
        svc = handler.path[len("/api/restart/"):]
        from .services.registry import SERVICE_MAP
        if svc not in SERVICE_MAP and svc != "controller":
            handler._json_response(400, {"error": f"Unknown service '{svc}'", "known": sorted(SERVICE_MAP.keys())})
            return
        handler._json_response(200, admin_svc.restart_service(svc))
        return

    # POST /api/batch-restart
    if handler.path == "/api/batch-restart":
        body = handler._read_json_body()
        services = body.get("services", [])
        if not services:
            handler._json_response(400, {"error": "services list required"})
            return
        handler._json_response(200, admin_svc.batch_restart(services))
        return

    # POST /api/rotate-keys
    if handler.path == "/api/rotate-keys":
        body = handler._read_json_body() or {}
        target = body.get("services")  # optional list of service IDs
        handler._json_response(200, admin_svc.rotate_keys(target))
        return

    # POST /api/reset-password
    if handler.path == "/api/reset-password":
        body = handler._read_json_body()
        new_password = body.get("password", "")
        if not new_password or len(new_password) < 4:
            handler._json_response(400, {"error": "password field required (min 4 chars)"})
            return
        target = body.get("services")  # optional list of service IDs
        handler._json_response(200, admin_svc.reset_password(new_password, target))
        return

    # POST /api/credentials -- ad-hoc credential revalidation
    if handler.path == "/api/credentials":
        body = handler._read_json_body() or {}
        target = body.get("services")  # optional list of service IDs
        handler._json_response(200, health_svc.probe_credentials(target))
        return

    # POST /api/services/{id}/api-key -- manually set or discover a service API key
    if handler.path.startswith("/api/services/") and handler.path.endswith("/api-key"):
        _handle_service_api_key_post(handler)
        return

    # POST /api/services/{id}/reset -- hard-reset a service (restart + re-discover key + re-run preflight)
    if handler.path.startswith("/api/services/") and handler.path.endswith("/reset"):
        svc_id = handler.path.split("/")[3]
        body = handler._read_json_body()
        handler._json_response(200, admin_svc.hard_reset_service(svc_id, body or {}))
        return

    # POST /api/routing
    if handler.path == "/api/routing":
        body = handler._read_json_body()
        if not body:
            handler._json_response(400, {"error": "JSON body required"})
            return
        handler._json_response(200, config_svc.update_routing(body, handler.action_trigger))
        return

    # POST /api/restore -- restore config from backup JSON
    if handler.path == "/api/restore":
        body = handler._read_json_body()
        if not body or "service_configs" not in body:
            handler._json_response(400, {"error": "backup JSON with service_configs required"})
            return
        handler._json_response(200, config_svc.restore_backup(body, handler.state))
        return

    # POST /api/media-server/reset -- hard-reset media server credentials via DB
    if handler.path == "/api/media-server/reset" or admin_svc.is_media_server_reset_path(handler.path):
        body = handler._read_json_body()
        username = body.get("username", os.environ.get("STACK_ADMIN_USERNAME", "admin"))
        password = body.get("password", os.environ.get("STACK_ADMIN_PASSWORD", "media-stack"))
        if not password or len(password) < 4:
            handler._json_response(400, {"error": "password required (min 4 chars)"})
            return
        handler._json_response(200, admin_svc.jellyfin_hard_reset(username, password))
        return

    # POST /api/gpu/enable -- auto-configure GPU transcoding in Jellyfin
    if handler.path == "/api/gpu/enable":
        handler._json_response(200, ops_svc.enable_gpu_transcoding())
        return

    # POST /api/snapshot -- take a config snapshot now
    if handler.path == "/api/snapshot":
        handler._json_response(200, ops_svc.take_snapshot())
        return

    # POST /api/guardrails
    if handler.path == "/api/guardrails":
        body = handler._read_json_body()
        if not body:
            handler._json_response(400, {"error": "JSON body required"})
            return
        handler._json_response(200, disk_svc.update_guardrails(body))
        return

    # POST /api/profile
    if handler.path == "/api/profile":
        body = handler._read_json_body()
        content = body.get("content", "")
        if not content:
            handler._json_response(400, {"error": "content field required"})
            return
        handler._json_response(200, config_svc.save_profile(content, handler.reload_config))
        return

    # POST /api/envvars
    if handler.path == "/api/envvars":
        body = handler._read_json_body()
        key = body.get("key", "")
        value = body.get("value", "")
        if not key:
            handler._json_response(400, {"error": "key field required"})
            return
        # Platform prefixes + service-derived prefixes from the registry
        _PLATFORM_PREFIXES = ("BOOTSTRAP_", "STACK_", "K8S_", "CONTROLLER_", "PUID", "PGID", "TZ")
        from .services.registry import SERVICES as _env_svcs
        _svc_prefixes = {s.api_key_env.split("_")[0] + "_" for s in _env_svcs if s.api_key_env}
        _allowed = set(_PLATFORM_PREFIXES) | _svc_prefixes
        if not any(key.startswith(p) for p in _allowed):
            handler._json_response(400, {"error": f"env var must start with a known prefix (BOOTSTRAP_, STACK_, K8S_, CONTROLLER_, or a registered service prefix)"})
            return
        handler._json_response(200, config_svc.set_envvar(key, value))
        return

    # POST /webhooks/test
    if handler.path == "/webhooks/test":
        handler._json_response(200, handler._test_webhook())
        return

    # POST /cancel or POST /actions/cancel -- cancel running action
    if handler.path in ("/cancel", "/actions/cancel"):
        cancelled = handler.state.cancel_action()
        handler._json_response(200, {
            "status": "cancel_requested" if cancelled else "no_action_running",
            "current_action": handler.state.current_action.to_dict() if handler.state.current_action else None,
        })
        return

    # POST /actions/{name}
    if handler.path.startswith("/actions/"):
        action_name = handler.path[len("/actions/"):]
        if action_name not in KNOWN_ACTIONS:
            handler._json_response(404, {"error": f"unknown action '{action_name}'", "known": sorted(KNOWN_ACTIONS)})
            return
        handler._handle_action(action_name)
        return

    # POST /config
    if handler.path == "/config":
        body = handler._read_json_body()
        if not body:
            handler._json_response(400, {"error": "JSON body required"})
            return
        updated = handler.state.update_config(body)
        logger.info("Config updated: %s", body)
        handler._json_response(200, {"status": "updated", "config": updated})
        return

    # POST /webhooks
    if handler.path == "/webhooks":
        body = handler._read_json_body()
        url = body.get("url", "").strip()
        if url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                handler._json_response(400, {"error": "Invalid webhook URL — must be http:// or https://"})
                return
            handler.state.webhook_urls.add(url)
        handler._json_response(200, {"webhook_urls": list(handler.state.webhook_urls)})
        return

    handler._json_response(404, {"error": "not found"})


# ---------------------------------------------------------------------------
# Helper functions for complex route handlers
# ---------------------------------------------------------------------------

def _handle_service_api_key_post(handler: ControllerAPIHandler) -> None:
    parts = handler.path.split("/")
    svc_id = parts[3] if len(parts) >= 5 else ""
    from media_stack.api.services.registry import SERVICE_MAP, read_api_key_from_file, read_api_key_via_http
    svc = SERVICE_MAP.get(svc_id)
    if not svc or not svc.api_key_env:
        handler._json_response(404, {"error": f"Service '{svc_id}' not found or has no API key"})
        return
    body = handler._read_json_body() or {}
    manual_key = str(body.get("api_key", "")).strip()
    if manual_key:
        os.environ[svc.api_key_env] = manual_key
        admin_svc.persist_keys_to_secret({svc.api_key_env: manual_key})
        handler._json_response(200, {"status": "set", "service": svc_id, "env": svc.api_key_env})
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
        handler._json_response(200, {"status": "discovered", "service": svc_id, "source": source})
    else:
        handler._json_response(404, {"error": f"Could not discover API key for {svc_id}. Provide it manually via api_key field."})
