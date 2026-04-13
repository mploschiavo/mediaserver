"""TRASHguides quality preset service.

Fetches preset definitions from recyclarr/config-templates on GitHub
and applies them to Sonarr/Radarr via their APIs.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Any


# Curated presets — each maps to a recyclarr config-template
PRESETS = [
    {
        "id": "web-1080p",
        "name": "WEB 1080p",
        "description": "Streaming services (Netflix, Amazon, etc.) in 1080p. Best for most users.",
        "app": "sonarr",
        "profile_url": "https://raw.githubusercontent.com/recyclarr/config-templates/main/sonarr/includes/quality-profiles/sonarr-v4-quality-profile-web-1080p.yml",
        "cf_url": "https://raw.githubusercontent.com/recyclarr/config-templates/main/sonarr/includes/custom-formats/sonarr-v4-custom-formats-web-1080p.yml",
    },
    {
        "id": "web-2160p",
        "name": "WEB 2160p (4K)",
        "description": "Streaming services in 4K with HDR. Requires 4K-capable display.",
        "app": "sonarr",
        "profile_url": "https://raw.githubusercontent.com/recyclarr/config-templates/main/sonarr/includes/quality-profiles/sonarr-v4-quality-profile-web-2160p.yml",
        "cf_url": "https://raw.githubusercontent.com/recyclarr/config-templates/main/sonarr/includes/custom-formats/sonarr-v4-custom-formats-web-2160p.yml",
    },
    {
        "id": "anime",
        "name": "Anime",
        "description": "Optimized for anime with dual audio and subtitle preferences.",
        "app": "sonarr",
        "profile_url": "",
        "cf_url": "https://raw.githubusercontent.com/recyclarr/config-templates/main/sonarr/includes/custom-formats/sonarr-v4-custom-formats-anime.yml",
    },
    {
        "id": "radarr-web-1080p",
        "name": "WEB 1080p (Movies)",
        "description": "Movie streaming services in 1080p.",
        "app": "radarr",
        "profile_url": "",
        "cf_url": "",
    },
    {
        "id": "radarr-web-2160p",
        "name": "WEB 2160p (Movies 4K)",
        "description": "Movie streaming in 4K with HDR.",
        "app": "radarr",
        "profile_url": "",
        "cf_url": "",
    },
]


class QualityPresetService:
    """Fetch and apply TRASHguides quality presets."""

    def list_presets(self) -> dict[str, Any]:
        """Return available quality presets."""
        return {"presets": PRESETS}

    def get_current_profiles(self, service_id: str) -> dict[str, Any]:
        """Get quality profiles from an arr service."""
        from media_stack.api.services.health import discover_api_keys
        from media_stack.api.services.registry import SERVICE_MAP
        from media_stack.core.http import HttpClient

        svc = SERVICE_MAP.get(service_id)
        if not svc:
            return {"error": f"Service {service_id} not found"}
        key = discover_api_keys().get(service_id, "")
        if not key:
            return {"error": f"No API key for {service_id}"}

        _http = HttpClient()
        _, profiles, _ = _http.request(
            f"http://{svc.host}:{svc.port}", "/api/v3/qualityprofile", api_key=key
        )
        if not isinstance(profiles, list):
            return {"error": "Failed to fetch profiles"}

        result = []
        for p in profiles:
            items = p.get("items", [])
            allowed = []
            for item in items:
                q = item.get("quality")
                if q and item.get("allowed"):
                    allowed.append(q.get("name", "?"))
                for sub in item.get("items", []):
                    sq = sub.get("quality")
                    if sq and sub.get("allowed", item.get("allowed")):
                        allowed.append(sq.get("name", "?"))
            result.append({
                "id": p["id"],
                "name": p["name"],
                "upgradeAllowed": p.get("upgradeAllowed", False),
                "cutoff": p.get("cutoff", 0),
                "allowed": allowed,
                "formatItems": len([f for f in p.get("formatItems", []) if f.get("score", 0) != 0]),
            })
        return {"profiles": result, "service": service_id}

    def get_custom_formats(self, service_id: str) -> dict[str, Any]:
        """Get existing custom formats from an arr service."""
        from media_stack.api.services.health import discover_api_keys
        from media_stack.api.services.registry import SERVICE_MAP
        from media_stack.core.http import HttpClient

        svc = SERVICE_MAP.get(service_id)
        if not svc:
            return {"error": f"Service {service_id} not found"}
        key = discover_api_keys().get(service_id, "")
        if not key:
            return {"error": f"No API key for {service_id}"}

        _http = HttpClient()
        _, cfs, _ = _http.request(
            f"http://{svc.host}:{svc.port}", "/api/v3/customformat", api_key=key
        )
        if not isinstance(cfs, list):
            return {"error": "Failed to fetch custom formats"}

        return {
            "custom_formats": [{"id": cf["id"], "name": cf["name"]} for cf in cfs],
            "total": len(cfs),
            "service": service_id,
        }

    def toggle_quality(self, service_id: str, profile_id: int, quality_name: str, enabled: bool) -> dict[str, Any]:
        """Enable or disable a quality level in a profile."""
        from media_stack.api.services.health import discover_api_keys
        from media_stack.api.services.registry import SERVICE_MAP
        from media_stack.core.http import HttpClient

        svc = SERVICE_MAP.get(service_id)
        if not svc:
            return {"error": f"Service {service_id} not found"}
        key = discover_api_keys().get(service_id, "")
        if not key:
            return {"error": f"No API key for {service_id}"}

        _http = HttpClient()
        _, profile, _ = _http.request(
            f"http://{svc.host}:{svc.port}", f"/api/v3/qualityprofile/{profile_id}", api_key=key
        )
        if not isinstance(profile, dict):
            return {"error": "Failed to fetch profile"}

        # Find and toggle the quality
        changed = False
        for item in profile.get("items", []):
            q = item.get("quality")
            if q and q.get("name") == quality_name:
                item["allowed"] = enabled
                changed = True
            for sub in item.get("items", []):
                sq = sub.get("quality")
                if sq and sq.get("name") == quality_name:
                    sub["allowed"] = enabled
                    changed = True

        if not changed:
            return {"error": f"Quality '{quality_name}' not found in profile"}

        _, result, _ = _http.request(
            f"http://{svc.host}:{svc.port}", f"/api/v3/qualityprofile/{profile_id}",
            api_key=key, method="PUT", payload=profile,
        )
        return {"status": "updated", "quality": quality_name, "enabled": enabled}

    def toggle_upgrade(self, service_id: str, profile_id: int, enabled: bool) -> dict[str, Any]:
        """Toggle upgradeAllowed on a quality profile."""
        from media_stack.api.services.health import discover_api_keys
        from media_stack.api.services.registry import SERVICE_MAP
        from media_stack.core.http import HttpClient

        svc = SERVICE_MAP.get(service_id)
        if not svc:
            return {"error": f"Service {service_id} not found"}
        key = discover_api_keys().get(service_id, "")
        if not key:
            return {"error": f"No API key for {service_id}"}

        _http = HttpClient()
        _, profile, _ = _http.request(
            f"http://{svc.host}:{svc.port}", f"/api/v3/qualityprofile/{profile_id}", api_key=key
        )
        if not isinstance(profile, dict):
            return {"error": "Failed to fetch profile"}

        profile["upgradeAllowed"] = enabled
        _http.request(
            f"http://{svc.host}:{svc.port}", f"/api/v3/qualityprofile/{profile_id}",
            api_key=key, method="PUT", payload=profile,
        )
        return {"status": "updated", "upgradeAllowed": enabled}


_instance = QualityPresetService()
list_presets = _instance.list_presets
get_current_profiles = _instance.get_current_profiles
get_custom_formats = _instance.get_custom_formats
toggle_quality = _instance.toggle_quality
toggle_upgrade = _instance.toggle_upgrade
