"""Download client category configuration."""
from __future__ import annotations

from typing import Any

from ._profile import ProfileService


class DownloadConfigService:
    """Manages torrent/usenet download categories."""

    def __init__(self, profile: ProfileService):
        self._profile = profile

    def get_download_categories(self) -> dict[str, Any]:
        from media_stack.services.app_config_service import load_app_config
        data, _ = self._profile.load()
        bindings = data.get("technology_bindings", {})
        tc_id = bindings.get("torrent_client", "qbittorrent")
        app_cfg = load_app_config(tc_id)
        if "categories" in app_cfg:
            return {"categories": app_cfg["categories"], "source": "app_config"}
        cats = data.get("download_categories")
        if isinstance(cats, dict) and cats:
            return {"categories": cats, "source": "profile"}
        return {"categories": {}, "source": "not_configured",
                "note": "Add categories in Config > Downloads"}

    def update_download_categories(self, categories: dict[str, str]) -> dict[str, Any]:
        if not categories:
            return {"error": "At least one category is required"}
        data, _ = self._profile.load()
        bindings = data.get("technology_bindings", {})
        tc_id = bindings.get("torrent_client", "qbittorrent")
        from media_stack.services.app_config_service import update_app_config_section
        result = update_app_config_section(tc_id, "categories", categories)
        if "error" not in result:
            result["categories"] = categories
            result["note"] = "Run configure-categories to apply changes"
        return result
