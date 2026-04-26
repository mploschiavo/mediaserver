"""Configure Arr download clients job — attach qBittorrent/SABnzbd to each Arr app.

Registered in contracts/services/sonarr.yaml as:
  configure-arr-clients:
    handler: media_stack.services.apps.servarr.configure_arr_clients_job:configure_arr_clients
"""

from __future__ import annotations

from typing import Any

import media_stack.services.runtime_platform as runtime_platform


class ServarrConfigureArrClientsJob:

    @staticmethod
    def _arr_services(ctx: Any) -> list[Any]:
        from media_stack.api.services.registry import SERVICES
        return [svc for svc in SERVICES if svc.category == "arr"]

    def configure_arr_clients(self, ctx: Any) -> dict[str, Any]:
        """Attach download clients (qBit + SAB) to each enabled Arr app."""
        arr_services = _arr_services(ctx)
        if not arr_services:
            return {"skipped": "no arr services registered"}

        try:
            from media_stack.services.apps.servarr.runtime.arr_ops import (
                detect_arr_api_base,
                ensure_arr_download_client,
            )
        except ImportError as exc:
            return {"error": f"arr_ops import failed: {exc}"[:200]}

        qbit_cfg = ctx.cfg.get("qbittorrent", {}) or {}
        if not qbit_cfg.get("url"):
            qbit_cfg = dict(qbit_cfg)
            qbit_cfg["url"] = ctx.service_url("qbittorrent")
        qbit_auth = {"username": ctx.admin_username, "password": ctx.admin_password}

        sab_cfg = ctx.cfg.get("sabnzbd", {}) or {}
        if not sab_cfg.get("url"):
            sab_cfg = dict(sab_cfg)
            sab_cfg["url"] = ctx.service_url("sabnzbd")
        sab_api_key = ctx.api_key("sabnzbd")
        sab_auth: dict[str, Any] = {"username": ctx.admin_username, "password": ctx.admin_password}
        if sab_api_key:
            sab_auth["api_key"] = sab_api_key

        configured = []
        errors = []
        for svc in arr_services:
            app_key = ctx.api_key(svc.id)
            app_url = ctx.service_url(svc.id)
            if not app_key or not app_url:
                runtime_platform.log(f"[DEBUG] {svc.id}: missing key or url, skipping client setup")
                continue
            app_payload = {"name": svc.name, "url": app_url}
            try:
                api_base = detect_arr_api_base(app_url, app_key) or "/api/v3"
                if qbit_cfg.get("url"):
                    ensure_arr_download_client(
                        app_payload, app_url, api_base, app_key, qbit_cfg, qbit_auth,
                    )
                if sab_cfg.get("url") and (sab_api_key or sab_auth.get("password")):
                    ensure_arr_download_client(
                        app_payload, app_url, api_base, app_key, sab_cfg, sab_auth,
                    )
                configured.append(svc.id)
                runtime_platform.log(f"[OK] {svc.id}: download clients attached")
            except Exception as exc:
                errors.append(f"{svc.id}: {exc}")
                runtime_platform.log(f"[WARN] {svc.id}: download client setup: {exc}")

        if not configured and errors:
            return {"error": "; ".join(errors)[:500]}
        return {"configured": configured, "errors": errors}


_instance = ServarrConfigureArrClientsJob()
configure_arr_clients = _instance.configure_arr_clients
_arr_services = _instance._arr_services
