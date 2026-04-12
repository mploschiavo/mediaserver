"""Download categories job handler.

Sets up torrent and usenet download categories across all
download clients that support them.

Registered in contracts/services/qbittorrent.yaml as:
  configure-categories:
    handler: media_stack.services.apps.qbittorrent.configure_categories_job:configure_categories
"""

from __future__ import annotations

import os
from typing import Any

import media_stack.services.runtime_platform as runtime_platform


def _build_arr_apps(ctx: Any) -> list[dict[str, Any]]:
    """Build arr_apps list from registry — same structure the runner uses."""
    from media_stack.api.services.registry import SERVICES
    apps = []
    for svc in SERVICES:
        if svc.category != "arr":
            continue
        key = ctx.api_key(svc.id)
        url = ctx.service_url(svc.id)
        if not key or not url:
            continue
        apps.append({
            "name": svc.name,
            "app_name": svc.name,
            "implementation": svc.name,
            "url": url,
            "api_key": key,
        })
    return apps


def configure_categories(ctx: Any) -> dict[str, Any]:
    """Set up download categories in torrent and usenet clients."""
    arr_apps = _build_arr_apps(ctx)
    if not arr_apps:
        return {"skipped": "no arr apps with API keys available"}

    results = []
    username = ctx.admin_username
    password = ctx.admin_password

    # qBittorrent categories
    try:
        from media_stack.services.apps.servarr.runtime.qbit_ops import setup_torrent_categories
        qbit_cfg = ctx.cfg.get("qbittorrent", {})
        if not qbit_cfg.get("url"):
            qbit_cfg["url"] = ctx.service_url("qbittorrent")
        setup_torrent_categories(arr_apps, qbit_cfg, username, password)
        results.append("qbittorrent")
        runtime_platform.log(f"[OK] qBittorrent: download categories configured")
    except ImportError:
        pass
    except Exception as exc:
        runtime_platform.log(f"[WARN] qBittorrent categories: {exc}")

    # SABnzbd categories
    try:
        from media_stack.services.apps.servarr.runtime.sab_ops import ensure_sabnzbd_categories
        sab_cfg = ctx.cfg.get("sabnzbd", {})
        sab_api_key = ctx.api_key("sabnzbd")
        if sab_api_key:
            if not sab_cfg.get("url"):
                sab_cfg["url"] = ctx.service_url("sabnzbd")
            ensure_sabnzbd_categories(arr_apps, sab_cfg, sab_api_key)
            results.append("sabnzbd")
            runtime_platform.log(f"[OK] SABnzbd: download categories configured")
    except ImportError:
        pass
    except Exception as exc:
        runtime_platform.log(f"[WARN] SABnzbd categories: {exc}")

    if not results:
        return {"skipped": "no download clients reachable"}
    return {"configured": results}
