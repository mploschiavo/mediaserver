"""Download client category configuration."""
from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
from typing import Any

from ._profile import ProfileService
import logging


class DownloadConfigService:
    """Manages torrent/usenet download categories."""

    def __init__(self, profile: ProfileService):
        self._profile = profile

    @staticmethod
    def _default_torrent_client_id() -> str:
        """Derive the default torrent client from the service registry capabilities."""
        try:
            from ..registry import SERVICES
            for svc in SERVICES:
                caps = getattr(svc, "capabilities", None) or {}
                if isinstance(caps, dict) and caps.get("torrent_client"):
                    return svc.id
        except Exception as exc:
            log_swallowed(exc)
        return ""

    def _torrent_client_id(self) -> str:
        data, _ = self._profile.load()
        bindings = data.get("technology_bindings", {})
        return bindings.get("torrent_client") or self._default_torrent_client_id()

    def get_download_categories(self) -> dict[str, Any]:
        from media_stack.services.app_config_service import load_app_config
        tc_id = self._torrent_client_id()
        if tc_id:
            app_cfg = load_app_config(tc_id)
            if "categories" in app_cfg:
                return {"categories": app_cfg["categories"], "source": "app_config"}
        # Fallback: check profile regardless of torrent client
        data, _ = self._profile.load()
        cats = data.get("download_categories")
        if isinstance(cats, dict) and cats:
            return {"categories": cats, "source": "profile"}
        return {"categories": {}, "source": "not_configured",
                "note": "Add categories in Config > Downloads"}

    def update_download_categories(self, categories: dict[str, str]) -> dict[str, Any]:
        if not categories:
            return {"error": "At least one category is required"}
        tc_id = self._torrent_client_id()
        if tc_id:
            from media_stack.services.app_config_service import update_app_config_section
            result = update_app_config_section(tc_id, "categories", categories)
            if "error" not in result:
                result["categories"] = categories
                result["note"] = "Run configure-categories to apply changes"
            return result
        # No torrent client — save to profile as fallback
        result = self._profile.update_section("download_categories", categories)
        if "error" not in result:
            result["categories"] = categories
            result["note"] = "Run configure-categories to apply changes"
        return result
