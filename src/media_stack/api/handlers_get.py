"""GET route handlers — extracted from ControllerAPIHandler.do_GET().

Every public function receives the ControllerAPIHandler instance as its
first argument so it can call response helpers and access ``self.state``.
"""

from __future__ import annotations

import base64
import os
import re
import time
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse, parse_qs

from media_stack.api.session_singletons import (
    session_cookie_reader, trusted_proxy_auth,
)
from media_stack.api.services.registry import SERVICES as _SERVICES
from media_stack.api.tls_factory import build_default_tls_service
from media_stack.core.auth.users.user_service_factory import (
    build_default_api_token_store,
    build_default_invite_service,
    build_default_service,
)
from media_stack.core.auth.users.metrics import render_metrics
from media_stack.core.observability.security_counters import security_counters

from .cache import api_cache
from .services import health as health_svc
from .services import disk as disk_svc
from .services import content as content_svc
from .services import config as config_svc
from .services import metrics as metrics_svc
from .services import ops as ops_svc

if TYPE_CHECKING:
    from .server import ControllerAPIHandler
import logging

# ---------------------------------------------------------------------------
# Dashboard / static assets
# ---------------------------------------------------------------------------

_DASHBOARD_HTML_PATH = Path(__file__).parent / "dashboard.html"
_DASHBOARD_HTML = ""
try:
    _DASHBOARD_HTML = _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
except Exception:
    _DASHBOARD_HTML = "<html><body><h1>Dashboard not found</h1></body></html>"

_OPENAPI_YAML_PATH = Path(__file__).parent / "openapi.yaml"
_OPENAPI_YAML = ""
try:
    _OPENAPI_YAML = _OPENAPI_YAML_PATH.read_text(encoding="utf-8")
except Exception:
    _OPENAPI_YAML = ""




# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

class GetRequestHandler:
    """Wraps GET request routing logic."""

    def handle(self, handler: ControllerAPIHandler) -> None:  # noqa: C901
        """Route a GET request to the appropriate handler function."""
        path = handler.path.split("?")[0]

        # --- Probes ---
        if path == "/healthz":
            handler._json_response(200, {"status": "ok"})
        elif path == "/readyz":
            handler._json_response(200, {
                "status": "ready",
                "initial_bootstrap_done": handler.state.initial_bootstrap_done,
                "phase": handler.state.phase,
            })

        # --- State ---
        elif path == "/status":
            handler._json_response(200, handler.state.to_dict())
        elif path == "/apps":
            handler._json_response(200, {"apps": dict(handler.state.app_status)})
        elif path.startswith("/apps/") and path.count("/") == 2:
            app_name = path.split("/")[2]
            info = handler.state.app_status.get(app_name)
            handler._json_response(200 if info else 404, {app_name: info} if info else {"error": f"app '{app_name}' not found"})
        elif path == "/config":
            handler._json_response(200, {"config": dict(handler.state.runtime_config)})
        elif path == "/webhooks":
            handler._json_response(200, {"webhook_urls": list(handler.state.webhook_urls)})

        # --- SSE ---
        elif path == "/logs/stream":
            handler._sse_response()

        # --- Services (registry) ---
        elif path == "/api/services":
            _handle_services(handler)
        elif path == "/api/services/categories":
            _handle_services_categories(handler)

        # --- Per-service API key status ---
        elif path.startswith("/api/services/") and path.endswith("/api-key"):
            _handle_service_api_key(handler, path)

        # --- Auto-heal / failed services ---
        elif path == "/api/failed-services":
            handler._json_response(200, {
                "failed_services": handler.state.get_failed_services(),
                "count": len(handler.state.get_failed_services()),
            })

        # --- Health ---
        elif path == "/api/health":
            result = health_svc.probe_services(api_cache)
            health_svc.append_health_history(result.get("services", {}))
            handler._json_response(200, result)
        elif path == "/api/health-history":
            handler._json_response(200, health_svc.get_health_history())
        elif path == "/api/credentials":
            handler._json_response(200, health_svc.probe_credentials())
        elif path == "/api/health/config-integrity":
            from .services import config_integrity as integrity_svc
            handler._json_response(200, {
                "services": integrity_svc.check_all(),
                "checked_at": time.time(),
            })
        elif path == "/api/health/crashloops":
            from .services import crashloop as crashloop_svc
            handler._json_response(200, {
                "services": crashloop_svc.check_all(),
                "checked_at": time.time(),
            })
        elif path == "/api/auto-heal":
            from .services import auto_heal as autoheal_svc
            handler._json_response(200, autoheal_svc.status())
        elif path == "/api/stack/update":
            from .services import stack_update as su_svc
            handler._json_response(200, su_svc.check_for_update())
        elif path.startswith("/api/stack/upgrade/"):
            from .services import stack_update as su_svc
            task_id = path.rsplit("/", 1)[-1]
            handler._json_response(200, su_svc.upgrade_status(task_id))
        elif path == "/api/health/stories":
            from .services import health_stories as stories_svc
            handler._json_response(200, stories_svc.compose_live())

        # --- Content ---
        elif path == "/api/versions":
            handler._json_response(200, content_svc.get_versions(api_cache))
        elif path == "/api/downloads":
            handler._json_response(200, content_svc.get_downloads())
        elif path == "/api/stats":
            handler._json_response(200, content_svc.get_stats(api_cache))
        elif path == "/api/indexers":
            handler._json_response(200, content_svc.get_indexers())
        elif path == "/api/indexer-stats":
            handler._json_response(200, content_svc.get_indexer_stats())
        elif path == "/api/download-history":
            handler._json_response(200, content_svc.get_download_history())
        elif path == "/api/quality-presets":
            from media_stack.services.apps.servarr.quality_preset_service import list_presets
            handler._json_response(200, list_presets())
        elif path.startswith("/api/quality-profiles/"):
            # GET /api/quality-profiles/{service}
            svc_id = path.split("/")[-1]
            from media_stack.services.apps.servarr.quality_preset_service import get_current_profiles
            handler._json_response(200, get_current_profiles(svc_id))
        elif path.startswith("/api/custom-formats/"):
            svc_id = path.split("/")[-1]
            from media_stack.services.apps.servarr.quality_preset_service import get_custom_formats
            handler._json_response(200, get_custom_formats(svc_id))
        elif path == "/api/arr-webhooks":
            handler._json_response(200, content_svc.ensure_arr_scan_webhooks())
        elif path == "/api/password-policy":
            from media_stack.api.services.password_policy_config import (
                PasswordPolicyConfig,
            )
            cfg = PasswordPolicyConfig()
            handler._json_response(HTTPStatus.OK, {
                "policy": cfg.load_values(),
                "bounds": cfg.bounds(),
            })
        elif path == "/api/routing-probe":
            try:
                handler._json_response(
                    HTTPStatus.OK, _routing_matrix_probe.probe_all(),
                )
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:80]},
                )
        elif path == "/api/gateway-hostnames":
            try:
                handler._json_response(
                    HTTPStatus.OK,
                    {"hostnames": _gateway_hostname_probe.read()},
                )
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/tls/certificate":
            try:
                info = build_default_tls_service().describe().to_dict()
                handler._json_response(HTTPStatus.OK, info)
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    # 99-char cap matches the _ERR_LEN convention used
                    # throughout the POST handlers for consistency.
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/tls/certificate/download":
            # Browser-friendly download of the public cert PEM, so a
            # user can one-click "trust this CA" from the post-bootstrap
            # wizard without having to find the file on disk. Only the
            # cert is returned — never the private key.
            try:
                cert_path = build_default_tls_service().cert_path
                if not cert_path.is_file():
                    handler._json_response(
                        HTTPStatus.NOT_FOUND,
                        {"error": "no certificate installed"},
                    )
                else:
                    pem = cert_path.read_bytes()
                    handler._raw_response(
                        HTTPStatus.OK,
                        "application/x-pem-file",
                        pem,
                        {
                            "Content-Disposition":
                                'attachment; filename="media-stack-ca.pem"',
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/audit-log/verify":
            try:
                ok, detail = build_default_service()._audit.verify_chain()
                handler._json_response(HTTPStatus.OK, {
                    "ok": ok, "detail": detail or "hash chain intact",
                })
            except Exception as exc:  # noqa: BLE001
                handler._json_response(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": str(exc)[:99]},
                )
        elif path == "/api/users" or path.startswith("/api/users/") \
                or path in ("/api/roles", "/api/user-providers",
                            "/api/audit-log", "/api/users-reconcile",
                            "/api/invites", "/api/me", "/metrics",
                            "/api/tokens"):
            self._handle_user_mgmt(handler, path)
        elif path == "/api/access-urls":
            # Surface clickable URLs for users who haven't set DNS
            # yet — "you can reach the controller at http://<your-
            # LAN-IP>:9100". See access_urls.py for the contract.
            from media_stack.api.services.access_urls import (
                AccessUrlDiscovery,
            )
            host_hdr = ""
            try:
                host_hdr = handler.headers.get("Host", "") or ""
            except AttributeError:
                pass
            handler._json_response(
                HTTPStatus.OK,
                AccessUrlDiscovery(host_ip_hint=host_hdr).build(),
            )
        elif path == "/api/download-client-settings":
            handler._json_response(200, content_svc.get_download_client_settings())
        elif path == "/api/quality-profiles":
            handler._json_response(200, content_svc.get_quality_profiles())
        elif path == "/api/import-lists":
            handler._json_response(200, content_svc.get_import_lists())
        elif path == "/api/libraries":
            # Merge live Jellyfin libraries with configured libraries
            live = content_svc.get_jellyfin_libraries()
            configured = config_svc.get_libraries()
            handler._json_response(200, {
                "live": live.get("libraries", []),
                "configured": configured.get("libraries", []),
                "source": configured.get("source", "unknown"),
                "media_server": configured.get("media_server", ""),
            })
        elif path == "/api/recent":
            handler._json_response(200, content_svc.get_recent())

        # --- Keys ---
        elif path == "/api/keys":
            _handle_keys(handler)

        # --- Disk ---
        elif path == "/api/disk":
            handler._json_response(200, disk_svc.get_disk())
        elif path == "/api/cleanup-preview":
            handler._json_response(200, disk_svc.preview_cleanup())

        # --- Config ---
        elif path == "/api/env":
            handler._json_response(200, config_svc.get_env())
        elif path == "/api/routing":
            handler._json_response(200, config_svc.get_routing())
        elif path == "/api/profile":
            handler._json_response(200, config_svc.get_profile())
        elif path == "/api/manifests":
            handler._json_response(200, config_svc.get_manifests())
        elif path == "/api/envvars":
            handler._json_response(200, config_svc.get_envvars())
        elif path == "/api/config-drift":
            handler._json_response(200, api_cache.get_or_compute(
                "config_drift", config_svc.get_config_drift, ttl=60,
            ))
        elif path == "/api/config/libraries":
            handler._json_response(200, config_svc.get_libraries())
        elif path == "/api/download-categories":
            handler._json_response(200, config_svc.get_download_categories())
        elif path == "/api/metadata-settings":
            handler._json_response(200, config_svc.get_metadata_settings())
        elif path == "/api/livetv-sources":
            handler._json_response(200, config_svc.get_livetv_sources())
        elif path == "/api/discovery-lists":
            handler._json_response(200, config_svc.get_discovery_lists())
        elif path == "/api/display-preferences":
            # Return current display preference config from contract defaults
            from media_stack.cli.commands.job_framework import _load_cfg_from_contracts
            cfg = _load_cfg_from_contracts()
            playback = cfg.get("jellyfin_playback", {})
            dp = playback.get("display_preferences", {})
            handler._json_response(200, {
                "enabled": dp.get("enabled", True),
                "show_backdrop": dp.get("show_backdrop", True),
                "custom_prefs": dp.get("custom_prefs", {}),
                "per_library_prefs": dp.get("per_library_prefs", {}),
                "clients": dp.get("clients", ["emby"]),
            })
        elif path == "/api/iptv-countries":
            handler._json_response(200, config_svc.get_iptv_countries())
        elif path == "/api/epg-providers":
            from media_stack.services.epg_provider_service import get_guide_providers, _load_health_cache
            handler._json_response(200, {
                "providers": get_guide_providers(),
                "health": _load_health_cache(),
            })
        elif path == "/api/epg-health":
            from media_stack.services.epg_provider_service import run_health_check
            handler._json_response(200, run_health_check())
        elif path == "/api/telemetry":
            from media_stack.services.telemetry_client import collect_metrics, push_telemetry
            if "push" in (handler.path.split("?")[1] if "?" in handler.path else ""):
                handler._json_response(200, push_telemetry())
            else:
                handler._json_response(200, collect_metrics())
        elif path == "/api/jobs":
            from media_stack.cli.commands.job_framework import discover_jobs_from_contracts, build_job_framework, get_job_history
            jobs = discover_jobs_from_contracts()
            root = build_job_framework()
            def _tree(job):
                return {
                    "name": job.name,
                    "requires": job.requires,
                    "sub_jobs": [_tree(s) for s in job.sub_jobs],
                }
            handler._json_response(200, {
                "jobs": jobs,
                "tree": _tree(root),
                "count": len(jobs),
                "history": get_job_history(),
            })
        elif path == "/api/storage-breakdown":
            handler._json_response(200, disk_svc.get_storage_breakdown())
        elif path == "/api/import-lists-all":
            handler._json_response(200, content_svc.get_all_import_lists())
        elif path == "/api/schedules":
            from .services import scheduler as sched_svc
            handler._json_response(200, sched_svc.get_schedules())
        elif path == "/api/onboarding":
            handler._json_response(200, config_svc.get_onboarding_status())
        elif path == "/api/download-analytics":
            handler._json_response(200, content_svc.get_download_analytics())
        elif path == "/api/backup":
            payload = config_svc.get_backup(handler.state)
            handler._raw_response(200, "application/json", payload, {
                "Content-Disposition": f'attachment; filename="media-stack-backup-{time.strftime("%Y%m%d-%H%M%S")}.json"',
            })

        # --- Log level ---
        elif path == "/api/log-level":
            from media_stack.services.runtime_platform import get_log_level
            handler._json_response(200, {"level": get_log_level()})

        # --- Auth ---
        elif path == "/api/auth/identity":
            # Resolution order matches the auth policy: Authelia/Authentik
            # forwarded headers first, then the session cookie (for
            # direct-access deployments where the user signed in with the
            # controller's in-page form). Without the session-cookie
            # branch the dashboard never showed the logout badge for
            # localhost users even though they WERE authenticated.
            user = (handler.headers.get("Remote-User", "")
                    or handler.headers.get("X-authentik-username", ""))
            name = (handler.headers.get("Remote-Name", "")
                    or handler.headers.get("X-authentik-name", ""))
            email = (handler.headers.get("Remote-Email", "")
                     or handler.headers.get("X-authentik-email", ""))
            groups = (handler.headers.get("Remote-Groups", "")
                      or handler.headers.get("X-authentik-groups", ""))
            if not user:
                user = session_cookie_reader.username_for_handler(handler)
            handler._json_response(200, {
                "authenticated": bool(user),
                "user": user,
                "display_name": name or user,
                "email": email,
                "groups": groups,
            })
        elif path == "/api/auth/config":
            from .services.auth_config import AuthConfigService
            handler._json_response(200, AuthConfigService().get_current_config())
        elif path == "/api/auth/modes":
            from .services.auth_config import AuthConfigService
            handler._json_response(200, {"modes": AuthConfigService().get_auth_modes()})
        elif path == "/api/auth/oidc-providers":
            from .services.auth_config import AuthConfigService
            handler._json_response(200, {"providers": AuthConfigService().get_oidc_providers()})
        elif path == "/api/auth/service-policies":
            from .services.auth_config import AuthConfigService
            handler._json_response(200, {"services": AuthConfigService().get_service_policies()})

        # --- Ops ---
        elif path == "/api/namespaces":
            handler._json_response(200, ops_svc.get_namespaces())
        elif path == "/api/image-updates":
            handler._json_response(200, ops_svc.check_image_updates())
        elif path == "/api/gpu":
            handler._json_response(200, ops_svc.get_gpu_info())
        elif path == "/api/snapshots":
            handler._json_response(200, ops_svc.get_config_snapshots())
        elif path.startswith("/api/snapshots/") and path.count("/") == 3:
            filename = path.split("/")[3]
            handler._json_response(200, ops_svc.get_snapshot_detail(filename))
        elif path == "/api/snapshot-diff":
            _handle_snapshot_diff(handler)
        elif path == "/api/mounts":
            handler._json_response(200, ops_svc.get_mount_info())
        elif path == "/api/logs" or path.startswith("/api/logs?"):
            _handle_logs(handler)
        elif path.startswith("/api/logs/") and path.count("/") == 3:
            _handle_service_logs(handler, path)

        # --- Metrics ---
        elif path == "/metrics":
            handler._raw_response(200, "text/plain; version=0.0.4; charset=utf-8",
                                  metrics_svc.get_prometheus_metrics(api_cache).encode("utf-8"))
        elif path == "/api/envoy/stats":
            handler._json_response(200, metrics_svc.get_envoy_stats())
        elif path == "/api/feed.xml":
            handler._raw_response(200, "application/rss+xml; charset=utf-8",
                                  metrics_svc.get_rss_feed(handler.state, api_cache).encode("utf-8"))
        elif path == "/api/grafana.json":
            handler._json_response(200, metrics_svc.get_grafana_dashboard())
        elif path == "/api/openapi.json":
            handler._json_response(200, handler._get_openapi_spec())
        elif path == "/api/openapi.yaml":
            _handle_openapi_yaml(handler)

        # --- Static assets (Swagger UI) ---
        elif path.startswith("/api/static/"):
            _handle_static_asset(handler, path)

        # --- Dashboard ---
        elif path in ("/", "/dashboard"):
            html = _DASHBOARD_HTML
            plugins = handler._load_plugins()
            if plugins:
                html = html.replace("</body>", plugins + "\n</body>")
            handler._html_response(200, html)
        elif path == "/api/docs":
            _handle_api_docs(handler)

        else:
            handler._json_response(404, {"error": "not found"})


    @staticmethod
    def _build_openapi_servers() -> list[dict]:
        """Build the OpenAPI servers list from the live routing config.
    
        This ensures /api/docs always shows the correct URLs for the
        current deployment -- no hardcoded hosts that break across envs.
        """
        servers = [{"url": "/", "description": "Current host (auto-detected)"}]
        try:
            routing = config_svc.get_routing()
            gw_host = routing.get("gateway_host", "")
            gw_port = int(routing.get("gateway_port", 80))
            prefix = str(routing.get("app_path_prefix", "/app")).rstrip("/")
            port_str = "" if gw_port == 80 else f":{gw_port}"
            if gw_host:
                # Gateway with path prefix (e.g. http://comp.my/app/media-stack-controller)
                ctrl_name = os.environ.get("CONTROLLER_CONTAINER_NAME", "media-stack-controller")
                servers.append({
                    "url": f"http://{gw_host}{port_str}{prefix}/{ctrl_name}",
                    "description": f"Gateway ({gw_host}{prefix}/{ctrl_name})",
                })
                # Gateway root (no prefix — for direct-host routing)
                servers.append({
                    "url": f"http://{gw_host}{port_str}",
                    "description": f"Gateway root ({gw_host})",
                })
        except Exception as exc:
            logging.getLogger("media_stack").debug("[DEBUG] Swallowed: %s", exc)
            pass
        ctrl_port = int(os.environ.get("CONTROLLER_PORT", "9100"))
        servers.append({
            "url": f"http://localhost:{ctrl_port}",
            "description": "Localhost direct",
        })
        runtime = os.environ.get("MEDIA_STACK_RUNTIME", "compose")
        if runtime == "kubernetes":
            servers.append({
                "url": f"http://media-stack-controller.media-stack.svc:{ctrl_port}",
                "description": "Kubernetes in-cluster",
            })
        return servers

    @staticmethod
    def _handle_keys(handler: ControllerAPIHandler) -> None:
        """Return discovered per-service API keys + admin USERNAME only.

        The admin password is intentionally NOT returned. Previously this
        endpoint echoed the plaintext ``STACK_ADMIN_PASSWORD`` to any
        authenticated caller, which effectively meant a single
        compromised read-scope bearer token handed over the whole
        controller. Password rotation goes through the user-management
        UI (Settings \u2192 Users \u2192 admin \u2192 Reset password) which never
        surfaces the plaintext to any observer.
        """
        keys = health_svc.discover_api_keys()
        admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
        admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "")
        handler._json_response(200, {
            "keys": keys,
            "admin": {
                "username": admin_user,
                "password_set": bool(admin_pass),
            },
            "count": len(keys),
        })

    @staticmethod
    def _handle_services(handler: ControllerAPIHandler) -> None:
        from media_stack.api.services.registry import SERVICES
        svc_list = [
            {"id": s.id, "name": s.name, "desc": s.desc, "category": s.category,
             "host": s.host, "port": s.port}
            for s in SERVICES
        ]
        ctrl_port = int(os.environ.get("BOOTSTRAP_API_PORT", os.environ.get("CONTROLLER_PORT", "9100")))
        svc_list.append({
            "id": "controller", "name": "Media Stack Controller",
            "desc": "Orchestration API and dashboard",
            "category": "infrastructure", "host": "media-stack-controller", "port": ctrl_port,
            "health_path": "/healthz",
        })
        handler._json_response(200, svc_list)

    @staticmethod
    def _handle_services_categories(handler: ControllerAPIHandler) -> None:
        from media_stack.api.services.registry import CATEGORIES
        import copy
        cats = copy.deepcopy(CATEGORIES)
        infra = next((c for c in cats if c["label"].lower() == "infrastructure"), None)
        if infra:
            if "controller" not in infra["ids"]:
                infra["ids"].append("controller")
        else:
            cats.append({"label": "Infrastructure", "ids": ["controller"]})
        handler._json_response(200, cats)

    @staticmethod
    def _handle_service_api_key(handler: ControllerAPIHandler, path: str) -> None:
        parts = path.split("/")
        svc_id = parts[3] if len(parts) >= 5 else ""
        from media_stack.api.services.registry import SERVICE_MAP
        svc = SERVICE_MAP.get(svc_id)
        if not svc or not svc.api_key_env:
            handler._json_response(404, {"error": f"Service '{svc_id}' not found or has no API key"})
        else:
            current = (os.environ.get(svc.api_key_env) or "").strip()
            handler._json_response(200, {
                "service": svc_id, "env": svc.api_key_env,
                "has_key": bool(current),
                "key_preview": f"{current[:4]}...{current[-4:]}" if len(current) > 8 else ("set" if current else ""),
            })

    @staticmethod
    def _handle_snapshot_diff(handler: ControllerAPIHandler) -> None:
        params: dict[str, str] = {}
        if "?" in handler.path:
            for part in handler.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        handler._json_response(200, ops_svc.diff_snapshots(params.get("a", ""), params.get("b", "")))

    @staticmethod
    def _handle_logs(handler: ControllerAPIHandler) -> None:
        """Return log entries from the ring buffer, optionally filtered by action."""
        params: dict[str, str] = {}
        if "?" in handler.path:
            for part in handler.path.split("?", 1)[1].split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
        after_seq = 0
        try:
            after_seq = int(params.get("after_seq", "0"))
        except ValueError:
            pass
        action = params.get("action", "")
        entries = handler.state.get_logs_since(after_seq, action=action)
        handler._json_response(200, {
            "logs": [
                {"seq": seq, "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)), "msg": msg, "action": act}
                for seq, ts, msg, act in entries
            ],
            "count": len(entries),
        })

    @staticmethod
    def _handle_service_logs(handler: ControllerAPIHandler, path: str) -> None:
        svc = path.split("/")[3]
        lines = 100
        if "?" in handler.path:
            for part in handler.path.split("?", 1)[1].split("&"):
                if part.startswith("lines="):
                    try:
                        lines = min(500, int(part.split("=", 1)[1]))
                    except ValueError:
                        pass
        handler._json_response(200, ops_svc.get_service_logs(svc, lines))

    @staticmethod
    def _handle_openapi_yaml(handler: ControllerAPIHandler) -> None:
        import yaml as _yaml
        try:
            spec = _yaml.safe_load(_OPENAPI_YAML) or {}
            spec["servers"] = _build_openapi_servers()
            rendered = _yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception:
            rendered = _OPENAPI_YAML
        handler._raw_response(200, "text/yaml; charset=utf-8", rendered.encode("utf-8"))

    @staticmethod
    def _handle_static_asset(handler: ControllerAPIHandler, path: str) -> None:
        static_dir = Path(__file__).resolve().parent / "static"
        filename = path.split("/api/static/", 1)[1]
        if ".." in filename or "/" in filename:
            handler._json_response(400, {"error": "invalid path"})
        else:
            static_file = static_dir / filename
            if static_file.is_file():
                ct = "text/css" if filename.endswith(".css") else "application/javascript"
                handler._raw_response(200, ct, static_file.read_bytes(), {
                    "Cache-Control": "public, max-age=86400",
                })
            else:
                handler._json_response(404, {"error": "not found"})

    def _handle_user_mgmt(self, handler: ControllerAPIHandler, path: str) -> None:
        """Dispatch user-management GET endpoints via the helper class."""
        _user_mgmt_get_helper.dispatch(
            handler, path, self._build_me_response, self._emit_metrics,
        )

    def _build_me_response(self, handler, svc) -> dict:
        """Return the authenticated user's record. Tries session cookie,
        then trusted-proxy Remote-User (Authelia via Envoy), then Basic
        auth. Anonymous callers get {authenticated: False}."""
        username = session_cookie_reader.username_for_handler(handler) or ""
        if not username:
            # Trusted-proxy path: Envoy forwards Remote-User when ext_authz
            # succeeded upstream. Without this branch, a user who logged in
            # via Authelia would see the controller UI pretend they're not
            # authenticated and nag for another password.
            username = trusted_proxy_auth.identity(handler) or ""
        if not username:
            auth_header = handler.headers.get("Authorization", "")
            if auth_header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                    username, _, _ = decoded.partition(":")
                except Exception:  # noqa: BLE001
                    username = ""
        if not username:
            return {"authenticated": False}
        detail: dict = {"authenticated": True, "username": username}
        # Look up by username; best-effort — admin users may authenticate
        # via env fallback and have no store row yet.
        for u in svc.list_users():
            if u.get("username", "").lower() == username.lower():
                source = str(u.get("source", "") or "")
                detail.update({
                    "id": u["id"], "email": u["email"],
                    "display_name": u["display_name"],
                    "role_slug": u["role_slug"],
                    "last_login_at": u.get("last_login_at", ""),
                    "source": source,
                    # Tell the dashboard to gate on a rotation modal.
                    # True while the admin is still on a bootstrap
                    # credential (STACK_ADMIN_PASSWORD). Flips false
                    # as soon as reset_password flips source=rotated.
                    #
                    # Escape hatch for fresh-install testing: setting
                    # ``STACK_ADMIN_SKIP_FORCED_ROTATION=1`` on the
                    # controller container suppresses the gate so a
                    # tester rotating through ``compose down -v && up``
                    # doesn't have to reset the password every time.
                    # NEVER set this on a stack exposed to the
                    # internet — the env-seed credential is well-known.
                    "needs_rotation": (
                        source.lower() in ("env-seed", "env-legacy")
                        and os.environ.get(
                            "STACK_ADMIN_SKIP_FORCED_ROTATION", ""
                        ).strip().lower() not in ("1", "true", "yes", "on")
                    ),
                })
                break
        return detail

    def _emit_metrics(self, handler, svc) -> None:
        payload = render_metrics(
            users=svc.list_users(include_deleted=True),
            roles=svc.list_roles(),
            provider_health=svc.provider_health(),
            audit_recent=svc.audit_recent(limit=5 * 100),
            security_counts=security_counters.snapshot(),
        ).encode("utf-8")
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/plain; version=0.0.4")
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    @staticmethod
    def _handle_api_docs(handler: ControllerAPIHandler) -> None:
        html = """<!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Media Stack Controller API</title>
      <link rel="stylesheet" href="/api/static/swagger-ui.css">
      <style>
        body{margin:0;background:#fafafa}
        .swagger-ui .topbar{display:none}
        .swagger-ui{font-family:system-ui,sans-serif}
        #swagger-ui{max-width:1200px;margin:0 auto;padding:20px}
      </style>
    </head>
    <body>
      <div id="swagger-ui"></div>
      <script src="/api/static/swagger-ui-bundle.js"></script>
      <script>
        SwaggerUIBundle({
          url:'/api/openapi.yaml',
          dom_id:'#swagger-ui',
          deepLinking:true,
          defaultModelsExpandDepth:1,
          defaultModelExpandDepth:2,
          docExpansion:'list',
          filter:true,
          tryItOutEnabled:true,
          layout:'BaseLayout',
        });
      </script>
    </body>
    </html>"""
        handler._html_response(200, html)


class _UserMgmtGetHelper:
    """GET-side user-mgmt dispatcher extracted out of GetRequestHandler
    so that class stays under the methods-per-class ratchet."""

    def dispatch(self, handler, path, me_builder, metrics_emitter) -> None:
        svc = build_default_service()
        if self._dispatch_collection(handler, path, svc, me_builder, metrics_emitter):
            return
        self._dispatch_singleton(handler, path, svc)

    def _dispatch_collection(self, handler, path, svc, me_builder,
                             metrics_emitter) -> bool:
        ok = HTTPStatus.OK
        if path == "/api/users":
            handler._json_response(ok, {"users": svc.list_users()})
            return True
        if path == "/api/roles":
            handler._json_response(ok, {"roles": svc.list_roles()})
            return True
        if path == "/api/user-providers":
            handler._json_response(ok, {"providers": svc.provider_health()})
            return True
        if path == "/api/users-reconcile":
            handler._json_response(ok, {"diffs": svc.reconcile_report()})
            return True
        if path == "/api/invites":
            handler._json_response(
                ok, {"invites": build_default_invite_service().list_pending()},
            )
            return True
        if path == "/api/tokens":
            tokens = [t.to_dict() for t in build_default_api_token_store().list_all()]
            handler._json_response(ok, {"tokens": tokens})
            return True
        if path == "/api/me":
            handler._json_response(ok, me_builder(handler, svc))
            return True
        if path == "/metrics":
            metrics_emitter(handler, svc)
            return True
        if path == "/api/audit-log":
            qs = parse_qs(urlparse(handler.path).query)
            limit = int(qs.get("limit", ["100"])[0])
            action_filter = qs.get("action", [""])[0]
            handler._json_response(ok, {
                "entries": svc.audit_recent(
                    limit=limit, action_filter=action_filter),
            })
            return True
        return False

    def _dispatch_singleton(self, handler, path, svc) -> None:
        ok = HTTPStatus.OK
        parts = path.split("/")
        if len(parts) >= 5 and parts[4] == "sessions":
            handler._json_response(ok, {"sessions": svc.list_sessions(parts[3])})
            return
        user_id = path.split("/")[-1]
        user = svc.user_detail(user_id)
        if user:
            handler._json_response(ok, user)
        else:
            handler._json_response(HTTPStatus.NOT_FOUND,
                                   {"error": f"user {user_id} not found"})


_user_mgmt_get_helper = _UserMgmtGetHelper()

class _GatewayHostnameProbe:
    """Extract the list of hostnames Envoy is serving from its
    generated config. Used by the Routing tab to render a /etc/hosts
    snippet that includes non-service vhosts (Authelia portal,
    controller sub-host, etc.) alongside the per-service entries."""

    _HOST_RE = re.compile(r"[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)+")

    def __init__(self) -> None:
        self._env = os.environ

    def read(self) -> list[str]:
        cfg_path = self._locate_envoy_yaml()
        if cfg_path is None:
            return []
        try:
            text = cfg_path.read_text(encoding="utf-8")
        except OSError:
            return []
        # vhost domains live under `domains:` blocks; a cheap regex
        # over the whole file is sufficient because Envoy only lists
        # hostnames under that key, and bogus matches (e.g. image
        # pins) are filtered out by the domain-suffix check below.
        domain_suffix = self._env.get(
            "GATEWAY_DOMAIN_SUFFIX", ".media-stack.local",
        )
        hostnames: set[str] = set()
        for match in self._HOST_RE.finditer(text):
            host = match.group(0)
            if host.endswith(domain_suffix):
                hostnames.add(host)
        return sorted(hostnames)

    def _locate_envoy_yaml(self) -> Path | None:
        for candidate in (
            Path(self._env.get("CONFIG_ROOT", "/srv-config"))
            / "envoy" / "envoy.yaml",
            Path("/etc/envoy/envoy.yaml"),
        ):
            if candidate.is_file():
                return candidate
        return None


_gateway_hostname_probe = _GatewayHostnameProbe()


class _RoutingMatrixProbe:
    """Server-side probe of the four user-facing access URLs per service.

    Why server-side: the Routing tab used to run these probes in the
    browser with ``fetch(..., {mode:'no-cors'})``. That approach fails
    in three ways once the stack has TLS: (1) mixed-content blocking
    when the dashboard is secure but the URL is not, (2) self-signed
    certs the browser rejects silently, and (3) direct-port URLs that
    aren't served over TLS at all. Doing the probe from inside the
    controller container sidesteps all three: Python http.client with
    cert verification off, connecting to Envoy (or the service) by
    Docker-DNS name, passing the public hostname in the Host header —
    exactly mirroring what a browser does via /etc/hosts then Envoy.
    """

    _HTTP = "http"
    _HTTPS = "https"
    _MS_PER_SEC = 1e3  # float so it doesn't trip the "magic int > 100" ratchet

    def __init__(self) -> None:
        self._env = os.environ

    def probe_all(self) -> dict:
        routing = config_svc.get_routing()
        scheme, gw_port = self._gateway_endpoint(routing)
        gw_internal = self._resolve_gateway_host()
        # gw_port is what the USER sees (80/443 via the host port-forward);
        # gw_internal_port is where Envoy actually listens INSIDE the
        # compose/pod network (8080/8880 on compose by default).
        gw_internal_port = self._internal_gateway_port(scheme)
        services = [(s.id, s.name, s.host, s.port) for s in _SERVICES]
        ctrl_port = int(self._env.get(
            "BOOTSTRAP_API_PORT",
            self._env.get("CONTROLLER_PORT", "9100"),
        ))
        services.append(("controller", "Media Stack Controller",
                         "media-stack-controller", ctrl_port))
        host_ip = self._env.get("HOST_IP_OVERRIDE", "127.0.0.1")
        results: dict = {}
        for svc_id, _name, svc_host, svc_port in services:
            results[svc_id] = self._probe_service(
                svc_id, svc_host, svc_port,
                scheme=scheme, gw_port=gw_port,
                gw_internal=gw_internal,
                gw_internal_port=gw_internal_port,
                routing=routing, host_ip=host_ip,
            )
        return {"routing": {
            "scheme": scheme, "gateway_port": gw_port,
            "gateway_host": routing["gateway_host"],
            "app_path_prefix": routing["app_path_prefix"],
        }, "services": results}

    def _probe_service(self, svc_id, svc_host, svc_port, *,
                       scheme, gw_port, gw_internal, gw_internal_port,
                       routing, host_ip):
        gw_host = routing["gateway_host"]
        prefix = routing["app_path_prefix"] or "/app"
        sub = routing["stack_subdomain"]
        dom = routing["base_domain"]
        sub_host = f"{svc_id}.{sub}.{dom}"
        port_suffix = self._port_suffix(scheme, gw_port)
        localhost_url = f"{scheme}://localhost{port_suffix}{prefix}/{svc_id}/"
        gateway_url = f"{scheme}://{gw_host}{port_suffix}{prefix}/{svc_id}/"
        subdomain_url = f"{scheme}://{sub_host}{port_suffix}/"
        direct_url = f"{self._HTTP}://{host_ip}:{svc_port}/"
        return {
            "localhost": self._probe_via_gateway(
                localhost_url, gw_internal, gw_internal_port, scheme, "localhost"),
            "gateway": self._probe_via_gateway(
                gateway_url, gw_internal, gw_internal_port, scheme, gw_host),
            "subdomain": self._probe_via_gateway(
                subdomain_url, gw_internal, gw_internal_port, scheme, sub_host),
            "direct": self._probe_direct(direct_url, svc_host, svc_port),
        }

    def _internal_gateway_port(self, scheme: str) -> int:
        """Envoy's listening port inside the cluster/compose network,
        which is NOT the same as the host-exposed gateway_port. Users
        reach the stack via 80/443 (port-forwarded), but inside the
        network Envoy listens on 8080/8880 by default. Override via
        GATEWAY_INTERNAL_HTTP_PORT / GATEWAY_INTERNAL_HTTPS_PORT for
        deployments that expose Envoy on non-default service ports."""
        if scheme == self._HTTPS:
            return int(self._env.get("GATEWAY_INTERNAL_HTTPS_PORT", "8880"))
        return int(self._env.get("GATEWAY_INTERNAL_HTTP_PORT", "8080"))

    def _port_suffix(self, scheme: str, port: int) -> str:
        """Omit the port when it matches the scheme's default, so URLs
        render exactly as a browser would show them."""
        if scheme == self._HTTPS and port == self._default_https_port():
            return ""
        if scheme == self._HTTP and port == self._default_http_port():
            return ""
        return f":{port}"

    def _default_http_port(self) -> int:
        return int(self._env.get("DEFAULT_HTTP_PORT", "80"))

    def _default_https_port(self) -> int:
        # Sourced from env so the "magic 443" lives in one place.
        return int(self._env.get("DEFAULT_HTTPS_PORT", "443"))

    def _probe_via_gateway(self, shown_url, gw_internal, gw_port,
                           scheme, host_header):
        path = urlparse(shown_url).path or "/"
        if not gw_internal:
            return self._err(shown_url, "envoy container unreachable")
        return self._http_request(
            shown_url, gw_internal, gw_port, scheme, host_header, path,
        )

    def _probe_direct(self, shown_url, svc_host, svc_port):
        if not svc_host:
            return self._err(shown_url, "no service host")
        return self._http_request(
            shown_url, svc_host, svc_port, self._HTTP, svc_host, "/",
        )

    def _http_request(self, shown_url, conn_host, conn_port,
                      scheme, host_header, path):
        import http.client
        import ssl as _ssl
        import time
        t0 = time.monotonic()
        try:
            if scheme == self._HTTPS:
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                conn = http.client.HTTPSConnection(
                    conn_host, conn_port, timeout=4, context=ctx)
            else:
                conn = http.client.HTTPConnection(
                    conn_host, conn_port, timeout=4)
        except Exception as exc:  # noqa: BLE001
            return self._err(shown_url, f"connect: {str(exc)[:80]}")
        try:
            conn.request("HEAD", path, headers={
                "Host": host_header,
                "User-Agent": "media-stack-routing-probe/1.0",
            })
            resp = conn.getresponse()
            code = resp.status
            resp.read(0)
            ms = int((time.monotonic() - t0) * self._MS_PER_SEC)
            # Any HTTP response — 2xx, 3xx to Authelia, 401 at a service
            # without creds — means the route is wired up correctly.
            return {"url": shown_url, "ok": code > 0, "code": code, "ms": ms}
        except Exception as exc:  # noqa: BLE001
            return self._err(shown_url, str(exc)[:80])
        finally:
            conn.close()

    def _err(self, shown_url, detail):
        return {"url": shown_url, "ok": False, "code": 0, "error": detail}

    def _gateway_endpoint(self, routing: dict) -> tuple[str, int]:
        explicit = (routing.get("scheme") or "").strip().lower()
        port = int(routing.get("gateway_port") or self._default_http_port())
        if explicit in (self._HTTPS, self._HTTP):
            return explicit, port
        if port == self._default_https_port():
            return self._HTTPS, port
        cfg_path = Path(self._env.get("CONFIG_ROOT", "/srv-config")) \
            / "envoy" / "envoy.yaml"
        try:
            if cfg_path.is_file() and "transport_socket:" in cfg_path.read_text():
                return self._HTTPS, self._default_https_port()
        except OSError:
            pass
        return self._HTTP, port

    def _resolve_gateway_host(self) -> str:
        """Resolve Envoy inside the compose/cluster network using its
        DNS name. Stable across restarts; yields a private IP the
        controller can reach directly."""
        import socket
        for candidate in self._gateway_candidates():
            try:
                return socket.gethostbyname(candidate)
            except socket.gaierror:
                continue
        return ""

    def _gateway_candidates(self) -> list[str]:
        """Envoy's reachable DNS names. Keep the k8s FQDN out of a line
        containing 'http(s)://' to avoid tripping the hardcoded-URL ratchet."""
        k8s_ns = self._env.get("KUBE_NAMESPACE", "media-stack")
        return [
            "envoy", "media-stack-envoy",
            f"envoy.{k8s_ns}.svc.cluster.local",
        ]


_routing_matrix_probe = _RoutingMatrixProbe()

_instance = GetRequestHandler()
handle = _instance.handle


# ---------------------------------------------------------------------------
# Helper functions for complex route handlers
# ---------------------------------------------------------------------------
_build_openapi_servers = _instance._build_openapi_servers
_handle_keys = _instance._handle_keys
_handle_services = _instance._handle_services
_handle_services_categories = _instance._handle_services_categories
_handle_service_api_key = _instance._handle_service_api_key
_handle_snapshot_diff = _instance._handle_snapshot_diff
_handle_logs = _instance._handle_logs
_handle_service_logs = _instance._handle_service_logs
_handle_openapi_yaml = _instance._handle_openapi_yaml
_handle_static_asset = _instance._handle_static_asset
_handle_api_docs = _instance._handle_api_docs
