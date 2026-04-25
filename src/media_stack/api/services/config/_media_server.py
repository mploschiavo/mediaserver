"""Media server library configuration."""
from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
from typing import Any

import yaml

from ._profile import ProfileService
# hoisted from per-method import to reduce CIRCULAR_IMPORT_RISK_RATCHET drift
# (app_config_service is a leaf module — no cycle)
from media_stack.services.app_config_service import (
    load_app_config,
    update_app_config_section,
)
from ..registry import _find_services_dir
import logging


class LibraryConfigService:
    """Manages media server library definitions."""

    def __init__(self, profile: ProfileService):
        self._profile = profile

    def get_libraries(self) -> dict[str, Any]:
        """Return configured libraries from per-app config, profile, or contract defaults."""
        ms_id = self._profile.media_server_id()
        app_cfg = load_app_config(ms_id) if ms_id else {}
        if "libraries" in app_cfg:
            return {"libraries": app_cfg["libraries"], "source": "app_config", "media_server": ms_id}
        data, _ = self._profile.load()
        ms_overrides = data.get(ms_id, {}) if ms_id else {}
        if isinstance(ms_overrides, dict) and "libraries" in ms_overrides:
            return {"libraries": ms_overrides["libraries"], "source": "profile", "media_server": ms_id}
        libs = []
        try:
            svc_dir = _find_services_dir()
            svc_yaml = (svc_dir / f"{ms_id}.yaml") if svc_dir and ms_id else None
            if svc_yaml and svc_yaml.is_file():
                svc_cfg = yaml.safe_load(svc_yaml.read_text(encoding="utf-8")) or {}
                libs = svc_cfg.get("defaults", {}).get("libraries", {}).get("libraries", [])
        except Exception as exc:
            log_swallowed(exc)
        return {"libraries": libs, "source": "defaults" if libs else "not_configured", "media_server": ms_id}

    def update_libraries(self, libraries: list[dict[str, Any]]) -> dict[str, Any]:
        for lib in libraries:
            if not lib.get("name") or not lib.get("collection_type") or not lib.get("paths"):
                return {"error": f"Each library needs name, collection_type, and paths. Invalid: {lib.get('name', '?')}"}
        ms_id = self._profile.media_server_id()
        if not ms_id:
            return {"error": "No media server configured"}
        result = update_app_config_section(ms_id, "libraries", libraries)
        if "error" not in result:
            result["libraries"] = libraries
            result["note"] = "Run configure-libraries to apply changes"
        return result
