"""Metadata language and country settings."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ._profile import ProfileService


class MetadataConfigService:
    """Manages metadata language, country, and preset selection."""

    _DEFAULTS_DIR: Path = Path(__file__).resolve().parents[5] / "contracts" / "ui_defaults"

    def __init__(self, profile: ProfileService):
        self._profile = profile

    @classmethod
    def _load_default_presets(cls) -> list[dict[str, str]]:
        """Load metadata presets from contracts/defaults/metadata_presets.yaml."""
        path = cls._DEFAULTS_DIR / "metadata_presets.yaml"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            presets = data.get("presets", [])
            return presets if isinstance(presets, list) else []
        except Exception:
            return [{"language": "en", "country": "US", "label": "English (US)"}]

    def get_metadata_settings(self) -> dict[str, Any]:
        data, _ = self._profile.load()
        meta = data.get("metadata", {})
        presets = data.get("metadata_presets")
        if not isinstance(presets, list) or not presets:
            presets = self._load_default_presets()
        return {
            "language": meta.get("language", "en"),
            "country": meta.get("country", "US"),
            "source": "profile" if meta else "defaults",
            "presets": presets,
        }

    def update_metadata_settings(self, language: str, country: str) -> dict[str, Any]:
        if not language or not country:
            return {"error": "language and country are required"}
        data, path = self._profile.load()
        if path is None:
            return {"error": "Profile file not found"}
        meta = data.get("metadata", {})
        if not isinstance(meta, dict):
            meta = {}
        meta["language"] = language
        meta["country"] = country
        data["metadata"] = meta
        result = self._profile.save(data, path)
        if "error" not in result:
            result["metadata"] = {"language": language, "country": country}
            result["note"] = "Run bootstrap to apply metadata settings to media server and Arr apps"
        return result
