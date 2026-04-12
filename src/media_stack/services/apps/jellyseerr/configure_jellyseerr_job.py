"""Job framework handler: configure Jellyseerr.

Seeds local admin user + writes settings.json with Jellyfin/Radarr/Sonarr
connections + syncs Jellyfin libraries + restarts Jellyseerr so the first
login shows a fully working app with no setup wizard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import media_stack.services.runtime_platform as runtime_platform
from media_stack.services.apps.jellyseerr.local_admin_ops import ensure_local_admin_user
from media_stack.services.apps.jellyseerr.orchestrator_ops import configure
from media_stack.services.apps.jellyseerr.runtime_ops import _jellyseerr_service


def _sync_jellyfin_libraries(api_key: str, config_root: str) -> None:
    """Discover Jellyfin libraries via Jellyseerr API, enable them, write to settings.json."""
    from media_stack.adapters.http_client import http_request

    base = "http://jellyseerr:5055"

    # GET available libraries from Jellyfin (via Jellyseerr proxy)
    status, libs, body = http_request(
        base, "/api/v1/settings/jellyfin/library?sync=true",
        api_key=api_key,
    )
    if status != 200 or not isinstance(libs, list) or not libs:
        runtime_platform.log(f"[WARN] Jellyseerr: could not fetch libraries (HTTP {status})")
        return

    # Enable all discovered libraries
    for lib in libs:
        lib["enabled"] = True
    runtime_platform.log(f"[INFO] Jellyseerr: enabling {len(libs)} Jellyfin libraries: "
                         + ", ".join(lib.get("name", "?") for lib in libs))

    # Libraries can only be set via settings.json (API is read-only for this field)
    settings_path = Path(config_root) / "jellyseerr" / "settings.json"
    if not settings_path.is_file():
        runtime_platform.log("[WARN] Jellyseerr: settings.json not found, skipping library sync")
        return

    settings = json.loads(settings_path.read_text())
    jf = settings.setdefault("jellyfin", {})
    jf["libraries"] = libs
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    runtime_platform.log(f"[OK] Jellyseerr: wrote {len(libs)} enabled libraries to settings.json")


def _restart_jellyseerr() -> None:
    """Restart Jellyseerr so it picks up settings.json changes."""
    from media_stack.api.services.admin import restart_service
    result = restart_service("jellyseerr")
    if result.get("status") == "error":
        runtime_platform.log(f"[WARN] Jellyseerr restart: {result.get('error')}")
    else:
        runtime_platform.log(f"[OK] Jellyseerr: restarted ({result.get('method', 'unknown')})")


def configure_jellyseerr(ctx: Any) -> dict[str, Any] | None:
    """Seed local admin + configure Jellyseerr + sync libraries + restart."""
    cfg = ctx.cfg

    jelly_cfg = cfg.get("jellyseerr") or {}
    if not jelly_cfg.get("enabled", True):
        return {"skipped": "jellyseerr disabled"}

    # Step 1: Seed local admin into SQLite DB — login works immediately.
    try:
        class _Svc:
            @staticmethod
            def log(msg): runtime_platform.log(msg)
            @staticmethod
            def bool_cfg(d, k, default): return bool(d.get(k, default))

        ensure_local_admin_user(_Svc(), cfg, ctx.config_root)
    except Exception as exc:
        runtime_platform.log(f"[WARN] Jellyseerr local-admin seed: {exc}")

    # Step 2: Build arr_apps + app_keys using ctx (framework-provided).
    arr_apps: list[dict[str, Any]] = []
    app_keys: dict[str, str] = {}

    for app_name, svc_id, root_folder in [
        ("Sonarr", "sonarr", "/media/tv"),
        ("Radarr", "radarr", "/media/movies"),
    ]:
        key = ctx.api_key(svc_id)
        url = ctx.service_url(svc_id)
        if not key or not url:
            runtime_platform.log(f"[WARN] Jellyseerr: {app_name} unavailable "
                                 f"(key={bool(key)}, url={bool(url)}), skipping")
            continue
        runtime_platform.log(f"[INFO] Jellyseerr: {app_name} API key resolved")
        app_keys[app_name] = key
        arr_apps.append({
            "name": app_name,
            "app_name": app_name,
            "implementation": app_name,
            "url": url,
            "api_key": key,
            "root_folder": root_folder,
        })

    # Step 3: Run orchestrator configure (API settings + settings.json).
    try:
        svc_instance = _jellyseerr_service(cfg)
        configure(svc_instance, cfg, arr_apps, app_keys, ctx.config_root, ctx.wait_timeout)
    except Exception as exc:
        runtime_platform.log(f"[WARN] Jellyseerr configure: {exc}")

    # Step 4: Sync Jellyfin libraries — enable all, write to settings.json.
    js_api_key = ctx.api_key("jellyseerr")
    if js_api_key:
        try:
            _sync_jellyfin_libraries(js_api_key, ctx.config_root)
        except Exception as exc:
            runtime_platform.log(f"[WARN] Jellyseerr library sync: {exc}")

    # Step 5: Restart Jellyseerr so it loads the updated settings.json.
    _restart_jellyseerr()

    return None
