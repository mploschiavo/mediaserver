"""Compatibility facade for Jellyseerr operation functions.

Implementation has been split into:
- infrastructure/jellyseerr/api_ops.py
- infrastructure/jellyseerr/file_ops.py
- application/jellyseerr/orchestrator_ops.py
"""

from __future__ import annotations

from media_stack.infrastructure.jellyseerr.api_ops import (
    ensure_jellyfin_settings,
    ensure_main_settings,
    ensure_radarr,
    ensure_sonarr,
)
from media_stack.infrastructure.jellyseerr.file_ops import configure_via_settings_file
from .orchestrator_ops import configure, permission_error

__all__ = [
    "ensure_main_settings",
    "ensure_jellyfin_settings",
    "ensure_radarr",
    "ensure_sonarr",
    "configure_via_settings_file",
    "permission_error",
    "configure",
]
