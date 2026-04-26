"""Configure auto-scan job — register webhook on Sonarr/Radarr so Jellyfin
refreshes its library the moment content lands.

Registered in contracts/services/sonarr.yaml as:
  configure-auto-scan:
    handler: media_stack.services.apps.servarr.configure_auto_scan_job:configure_auto_scan
"""

from __future__ import annotations

import os
from typing import Any

import media_stack.services.runtime_platform as runtime_platform


class ServarrConfigureAutoScanJob:

    @staticmethod
    def _controller_url() -> str:
        port = os.environ.get("BOOTSTRAP_API_PORT", os.environ.get("CONTROLLER_API_PORT", "9100"))
        host = os.environ.get("CONTROLLER_HOST", "media-stack-controller")
        return f"http://{host}:{port}"

    def configure_auto_scan(self, ctx: Any) -> dict[str, Any]:
        """Register /webhooks/arr on every Arr app so import events scan Jellyfin."""
        try:
            from media_stack.api.services.content import ensure_arr_scan_webhooks
        except ImportError as exc:
            return {"error": f"ensure_arr_scan_webhooks import failed: {exc}"[:200]}

        controller_url = _controller_url()
        try:
            result = ensure_arr_scan_webhooks(controller_url=controller_url)
        except Exception as exc:
            runtime_platform.log(f"[WARN] configure-auto-scan: {exc}")
            return {"error": str(exc)[:200]}

        webhooks = result.get("webhooks", {})
        registered = [s for s, v in webhooks.items() if v == "registered"]
        already = [s for s, v in webhooks.items() if v == "already registered"]
        errors = {s: v for s, v in webhooks.items() if v not in {"registered", "already registered"}}
        runtime_platform.log(
            f"[OK] Auto-scan webhook: registered={registered}, already={already}, errors={errors}"
        )
        return {"registered": registered, "already": already, "errors": errors, "url": result.get("url", "")}


_instance = ServarrConfigureAutoScanJob()
configure_auto_scan = _instance.configure_auto_scan
_controller_url = _instance._controller_url
