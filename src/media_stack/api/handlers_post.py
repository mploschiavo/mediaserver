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

# Core actions (not from contracts)
_CORE_ACTIONS = {
    "bootstrap", "post-setup", "discover-indexers", "restart-apps",
    "push-indexers", "envoy-config", "reconcile", "validate-credentials",
    "configure-media-server",
}


# KNOWN_ACTIONS initialized after class (needs _build_known_actions)


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

class PostRequestHandler:
    """Wraps POST request routing logic."""

    def handle(self, handler: ControllerAPIHandler) -> None:  # noqa: C901
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

        # POST /api/log-level -- change log level at runtime (no restart needed)
        if handler.path == "/api/log-level":
            body = handler._read_json_body() or {}
            level = body.get("level", "").upper()
            if level not in ("DEBUG", "INFO", "WARN", "ERROR"):
                handler._json_response(400, {
                    "error": f"Invalid log level '{level}'",
                    "valid": ["DEBUG", "INFO", "WARN", "ERROR"],
                })
                return
            from media_stack.services.runtime_platform import set_log_level, log
            new_level = set_log_level(level)
            log(f"[INFO] Log level changed to {new_level}")
            # Persist so it survives restarts
            handler.state.update_config({"_log_level": new_level})
            handler._json_response(200, {"level": new_level})
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

        # POST /api/libraries
        if handler.path == "/api/libraries":
            body = handler._read_json_body()
            libraries = body.get("libraries", [])
            if not isinstance(libraries, list):
                handler._json_response(400, {"error": "libraries must be an array"})
                return
            result = config_svc.update_libraries(libraries)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("configure-libraries", {})
                result["action"] = "configure-libraries queued"
            handler._json_response(200, result)
            return

        # POST /api/download-categories
        if handler.path == "/api/download-categories":
            body = handler._read_json_body()
            categories = body.get("categories", {})
            if not isinstance(categories, dict):
                handler._json_response(400, {"error": "categories must be an object {name: path}"})
                return
            handler._json_response(200, config_svc.update_download_categories(categories))
            return

        # POST /api/metadata-settings
        if handler.path == "/api/metadata-settings":
            body = handler._read_json_body()
            handler._json_response(200, config_svc.update_metadata_settings(
                body.get("language", ""), body.get("country", ""),
            ))
            return

        # POST /api/discovery-lists
        if handler.path == "/api/discovery-lists":
            body = handler._read_json_body()
            lists = body.get("lists")
            if not isinstance(lists, list):
                handler._json_response(400, {"error": "lists array required"})
                return
            result = config_svc.update_discovery_lists(lists)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("bootstrap", {})
                result["action"] = "bootstrap queued"
            handler._json_response(200, result)
            return

        # POST /api/display-preferences
        if handler.path == "/api/display-preferences":
            body = handler._read_json_body()
            # Update the playback display_preferences section in the per-app config
            from media_stack.services.app_config_service import load_app_config, save_app_config
            ms_id = config_svc._media_server_id()
            if not ms_id:
                handler._json_response(400, {"error": "No media server configured"})
                return
            app_cfg = load_app_config(ms_id)
            playback = app_cfg.setdefault("playback", {})
            dp = playback.setdefault("display_preferences", {})
            if "show_backdrop" in body:
                dp["show_backdrop"] = bool(body["show_backdrop"])
            if "custom_prefs" in body and isinstance(body["custom_prefs"], dict):
                dp["custom_prefs"] = body["custom_prefs"]
            if "per_library_prefs" in body and isinstance(body["per_library_prefs"], dict):
                dp["per_library_prefs"] = body["per_library_prefs"]
            result = save_app_config(ms_id, app_cfg)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("configure-playback", {})
                result["action"] = "configure-playback queued"
            handler._json_response(200, result)
            return

        # POST /api/quality-profiles/toggle
        if handler.path == "/api/quality-profiles/toggle":
            body = handler._read_json_body()
            from media_stack.services.apps.servarr.quality_preset_service import toggle_quality, toggle_upgrade
            if "quality" in body:
                handler._json_response(200, toggle_quality(
                    body["service"], int(body["profile_id"]), body["quality"], bool(body["enabled"])
                ))
            elif "upgradeAllowed" in body:
                handler._json_response(200, toggle_upgrade(
                    body["service"], int(body["profile_id"]), bool(body["upgradeAllowed"])
                ))
            else:
                handler._json_response(400, {"error": "quality or upgradeAllowed required"})
            return

        # POST /api/download-client-settings
        if handler.path == "/api/download-client-settings":
            body = handler._read_json_body()
            from .services import content as content_svc_dl
            handler._json_response(200, content_svc_dl.update_download_client_settings(body))
            return

        # POST /api/livetv-sources
        if handler.path == "/api/livetv-sources":
            body = handler._read_json_body()
            result = config_svc.update_livetv_sources(
                tuners=body.get("tuners"), guides=body.get("guides"),
                tuner_url=body.get("tuner_url", ""), guide_url=body.get("guide_url", ""),
                load_all_tuners=body.get("load_all_tuners"),
            )
            # Auto-trigger targeted Live TV reconfigure (not full bootstrap)
            if "error" not in result and handler.action_trigger:
                handler.action_trigger("configure-livetv", {})
                result["action"] = "configure-livetv queued"
            handler._json_response(200, result)
            return

        # POST /api/indexers/{id}/toggle
        if handler.path.startswith("/api/indexers/") and handler.path.endswith("/toggle"):
            parts = handler.path.split("/")
            try:
                indexer_id = int(parts[3])
            except (IndexError, ValueError):
                handler._json_response(400, {"error": "Invalid indexer ID"})
                return
            body = handler._read_json_body()
            from .services import content as content_svc_toggle
            handler._json_response(200, content_svc_toggle.toggle_indexer(indexer_id, bool(body.get("enable", True))))
            return

        # DELETE /api/indexers/{id}
        if handler.path.startswith("/api/indexers/") and handler.path.count("/") == 3:
            parts = handler.path.split("/")
            try:
                indexer_id = int(parts[3])
            except (IndexError, ValueError):
                handler._json_response(400, {"error": "Invalid indexer ID"})
                return
            body = handler._read_json_body()
            if body.get("_method") == "DELETE":
                from .services import content as content_svc_del
                handler._json_response(200, content_svc_del.delete_indexer(indexer_id))
                return

        # POST /webhooks/arr — receives Sonarr/Radarr webhook on download/import
        # Triggers Jellyfin library scan so new content appears immediately.
        if handler.path == "/webhooks/arr":
            body = handler._read_json_body()
            event = body.get("eventType", "unknown")
            title = ""
            if body.get("movie"):
                title = body["movie"].get("title", "")
            elif body.get("series"):
                title = body["series"].get("title", "")
            elif body.get("episodes"):
                eps = body["episodes"]
                title = eps[0].get("title", "") if eps else ""
            import media_stack.services.runtime_platform as _rp
            _rp.log(f"[INFO] Arr webhook: {event} — {title or 'unknown'}")
            # Scan on import/download events
            if event in ("Download", "EpisodeFileDelete", "MovieFileDelete", "MovieAdded", "SeriesAdd", "Grab"):
                try:
                    from .services.health import discover_api_keys
                    from .services.registry import SERVICE_MAP
                    api_key = discover_api_keys().get("jellyfin", "")
                    ms = SERVICE_MAP.get("jellyfin")
                    if ms and api_key:
                        import urllib.request
                        urllib.request.urlopen(urllib.request.Request(
                            f"http://{ms.host}:{ms.port}/Library/Refresh?api_key={api_key}",
                            method="POST",
                        ), timeout=5)
                        _rp.log(f"[OK] Jellyfin scan triggered by arr webhook ({event})")
                except Exception as exc:
                    _rp.log(f"[WARN] Jellyfin scan from webhook failed: {exc}")
            handler._json_response(200, {"status": "ok", "event": event})
            return

        # POST /api/import-lists/{service}/{id}/toggle
        if handler.path.startswith("/api/import-lists/") and handler.path.endswith("/toggle"):
            parts = handler.path.split("/")
            if len(parts) >= 5:
                svc_id = parts[3]
                try:
                    list_id = int(parts[4])
                except ValueError:
                    handler._json_response(400, {"error": "Invalid list ID"})
                    return
                body = handler._read_json_body()
                enabled = body.get("enabled", True)
                from .services import content as content_svc_toggle
                handler._json_response(200, content_svc_toggle.toggle_import_list(svc_id, list_id, enabled))
                return

        # POST /api/import-lists/{service}/{id}/delete
        if handler.path.startswith("/api/import-lists/") and handler.path.endswith("/delete"):
            parts = handler.path.split("/")
            if len(parts) >= 5:
                svc_id = parts[3]
                try:
                    list_id = int(parts[4])
                except ValueError:
                    handler._json_response(400, {"error": "Invalid list ID"})
                    return
                from .services import content as content_svc_list
                handler._json_response(200, content_svc_list.delete_import_list(svc_id, list_id))
                return

        # POST /api/schedules
        if handler.path == "/api/schedules":
            body = handler._read_json_body()
            from .services import scheduler as sched_svc
            handler._json_response(200, sched_svc.add_schedule(
                body.get("action", ""), int(body.get("interval_seconds", 0)),
                body.get("label", ""),
            ))
            return

        # POST /api/schedules/{id}/delete
        if handler.path.startswith("/api/schedules/") and handler.path.endswith("/delete"):
            parts = handler.path.split("/")
            try:
                sched_id = int(parts[3])
            except (IndexError, ValueError):
                handler._json_response(400, {"error": "Invalid schedule ID"})
                return
            from .services import scheduler as sched_svc_del
            handler._json_response(200, sched_svc_del.remove_schedule(sched_id))
            return

        # POST /api/validate-migration
        if handler.path == "/api/validate-migration":
            body = handler._read_json_body()
            handler._json_response(200, disk_svc.validate_migration_target(body.get("target_path", "")))
            return

        # POST /api/custom-service
        if handler.path == "/api/custom-service":
            body = handler._read_json_body()
            if not body:
                handler._json_response(400, {"error": "JSON body required"})
                return
            handler._json_response(200, config_svc.add_custom_service(body))
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
                # Persist webhooks so they survive restarts
                handler.state.update_config({"_webhook_urls": list(handler.state.webhook_urls)})
            handler._json_response(200, {"webhook_urls": list(handler.state.webhook_urls)})
            return

        handler._json_response(404, {"error": "not found"})


    @staticmethod
    def _build_known_actions() -> frozenset[str]:
        """Build KNOWN_ACTIONS from core actions + contract-discovered jobs."""
        actions = set(_CORE_ACTIONS)
        try:
            from media_stack.cli.commands.job_framework import discover_jobs_from_contracts
            for job in discover_jobs_from_contracts():
                actions.add(job["name"])
        except Exception:
            pass
        return frozenset(actions)

    @staticmethod
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


_instance = PostRequestHandler()
handle = _instance.handle


# ---------------------------------------------------------------------------
# Helper functions for complex route handlers
# ---------------------------------------------------------------------------
_build_known_actions = _instance._build_known_actions
_handle_service_api_key_post = _instance._handle_service_api_key_post
KNOWN_ACTIONS = _build_known_actions()
