"""GET route handlers — extracted from ControllerAPIHandler.do_GET().

Every public function receives the ControllerAPIHandler instance as its
first argument so it can call response helpers and access ``self.state``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .cache import api_cache
from .services import health as health_svc
from .services import disk as disk_svc
from .services import content as content_svc
from .services import config as config_svc
from .services import metrics as metrics_svc
from .services import ops as ops_svc

if TYPE_CHECKING:
    from .server import ControllerAPIHandler

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
    except Exception:
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


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def handle(handler: ControllerAPIHandler) -> None:  # noqa: C901
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
    elif path == "/api/quality-profiles":
        handler._json_response(200, content_svc.get_quality_profiles())
    elif path == "/api/import-lists":
        handler._json_response(200, content_svc.get_import_lists())
    elif path == "/api/libraries":
        handler._json_response(200, content_svc.get_jellyfin_libraries())
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
    elif path == "/api/libraries":
        handler._json_response(200, config_svc.get_libraries())
    elif path == "/api/download-categories":
        handler._json_response(200, config_svc.get_download_categories())
    elif path == "/api/metadata-settings":
        handler._json_response(200, config_svc.get_metadata_settings())
    elif path == "/api/livetv-sources":
        handler._json_response(200, config_svc.get_livetv_sources())
    elif path == "/api/discovery-lists":
        handler._json_response(200, config_svc.get_discovery_lists())
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


# ---------------------------------------------------------------------------
# Helper functions for complex route handlers
# ---------------------------------------------------------------------------

def _handle_keys(handler: ControllerAPIHandler) -> None:
    """Return all discovered API keys and admin credentials."""
    keys = health_svc.discover_api_keys()
    admin_user = os.environ.get("STACK_ADMIN_USERNAME", "admin")
    admin_pass = os.environ.get("STACK_ADMIN_PASSWORD", "media-stack")
    handler._json_response(200, {
        "keys": keys,
        "admin": {"username": admin_user, "password": admin_pass},
        "count": len(keys),
    })


def _handle_services(handler: ControllerAPIHandler) -> None:
    from media_stack.api.services.registry import SERVICES
    svc_list = [
        {"id": s.id, "name": s.name, "desc": s.desc, "category": s.category,
         "host": s.host, "port": s.port}
        for s in SERVICES
    ]
    ctrl_port = int(os.environ.get("CONTROLLER_PORT", "9876"))
    svc_list.append({
        "id": "controller", "name": "Media Stack Controller",
        "desc": "Orchestration API and dashboard",
        "category": "infrastructure", "host": "localhost", "port": ctrl_port,
    })
    handler._json_response(200, svc_list)


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


def _handle_snapshot_diff(handler: ControllerAPIHandler) -> None:
    params: dict[str, str] = {}
    if "?" in handler.path:
        for part in handler.path.split("?", 1)[1].split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v
    handler._json_response(200, ops_svc.diff_snapshots(params.get("a", ""), params.get("b", "")))


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


def _handle_openapi_yaml(handler: ControllerAPIHandler) -> None:
    import yaml as _yaml
    try:
        spec = _yaml.safe_load(_OPENAPI_YAML) or {}
        spec["servers"] = _build_openapi_servers()
        rendered = _yaml.dump(spec, default_flow_style=False, sort_keys=False, allow_unicode=True)
    except Exception:
        rendered = _OPENAPI_YAML
    handler._raw_response(200, "text/yaml; charset=utf-8", rendered.encode("utf-8"))


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
