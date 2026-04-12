"""Shared path resolution utilities for API services."""

from __future__ import annotations

import os
from pathlib import Path

_IMAGE_CONFIG = "/opt/media-stack/contracts/media-stack.config.json"
_IMAGE_PROFILE = "/opt/media-stack/contracts/media-stack.profile.yaml"


class ResolveService:
    """Path resolution for bootstrap config and profile files."""

    def resolve_config_path(self, candidate: str | None = None) -> str | None:
        """Resolve bootstrap config JSON path, trying multiple locations."""
        candidates = [
            candidate,
            os.environ.get("BOOTSTRAP_CONFIG_FILE"),
            _IMAGE_CONFIG,
        ]
        for p in candidates:
            if p and Path(p).is_file():
                return p
        return None

    def resolve_profile_path(self, candidate: str | None = None) -> str | None:
        """Resolve bootstrap profile YAML path, trying multiple locations."""
        candidates = [
            candidate,
            os.environ.get("BOOTSTRAP_PROFILE_FILE"),
            _IMAGE_PROFILE,
        ]
        for p in candidates:
            if p and Path(p).is_file():
                return p
        return None


_instance = ResolveService()

# Backward compat — callers use module-level functions
resolve_config_path = _instance.resolve_config_path
resolve_profile_path = _instance.resolve_profile_path
