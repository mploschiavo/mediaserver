"""Configure indexers job — run the Prowlarr indexer pipeline and sync to Arr apps.

Registered in contracts/services/prowlarr.yaml as:
  configure-indexers:
    handler: media_stack.services.apps.prowlarr.configure_indexers_job:configure_indexers
"""

from __future__ import annotations

import os
from typing import Any

import media_stack.services.runtime_platform as runtime_platform


class ProwlarrConfigureIndexersJob:

    @staticmethod
    def _build_arr_apps(ctx: Any) -> list[dict[str, Any]]:
        from media_stack.core.service_registry.registry import SERVICES
        apps: list[dict[str, Any]] = []
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

    def configure_indexers(self, ctx: Any) -> dict[str, Any]:
        """Run Prowlarr indexer pipeline: add configured indexers, sync to Arr apps."""
        prowlarr_url = ctx.service_url("prowlarr")
        prowlarr_key = ctx.api_key("prowlarr")
        if not prowlarr_url or not prowlarr_key:
            return {"skipped": "prowlarr not reachable or missing API key"}

        prowlarr_cfg = ctx.cfg.get("prowlarr", {}) or {}
        prowlarr_indexers = prowlarr_cfg.get("indexers") or []
        auto_add = bool(prowlarr_cfg.get("auto_indexers", os.environ.get("PROWLARR_AUTO_INDEXERS", "")))
        trigger_sync = bool(prowlarr_cfg.get("trigger_sync", True))

        arr_apps = _build_arr_apps(ctx)
        app_keys: dict[str, str] = {app["name"].lower(): app["api_key"] for app in arr_apps}

        try:
            from media_stack.application.prowlarr.runtime_ops import (
                run_prowlarr_indexer_pipeline,
            )
            result = run_prowlarr_indexer_pipeline(
                cfg=ctx.cfg,
                prowlarr_url=prowlarr_url,
                prowlarr_key=prowlarr_key,
                wait_timeout=ctx.wait_timeout,
                prowlarr_indexers=prowlarr_indexers,
                auto_indexers=auto_add,
                trigger_sync=trigger_sync,
                arr_apps_raw=arr_apps,
                app_keys=app_keys,
            )
        except Exception as exc:
            runtime_platform.log(f"[WARN] configure-indexers: {exc}")
            return {"error": str(exc)[:200]}

        runtime_platform.log(
            f"[OK] Prowlarr: indexer pipeline completed "
            f"(arr_apps={len(arr_apps)}, indexers={len(prowlarr_indexers)}, auto_add={auto_add})"
        )
        return {"arr_apps": len(arr_apps), "indexers": len(prowlarr_indexers), "result": result}


_instance = ProwlarrConfigureIndexersJob()
configure_indexers = _instance.configure_indexers
_build_arr_apps = _instance._build_arr_apps
