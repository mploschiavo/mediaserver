"""Media-server adapter port implementations.

ADR-0002 Phase 16-E (media_server_adapters) — concrete adapter
classes that satisfy the ``MediaServerAdapterBase`` port. The pure
protocol/value-object types live in
``media_stack.domain.media_server_adapters``; the factory + phase-
plan orchestration that drives these adapters lives in
``media_stack.application.media_server_adapters``.

Note: app-specific adapters (e.g. ``services.apps.jellyfin.media_server_adapter``,
``services.apps.plex.media_server_adapter``) are loaded dynamically
from the service contracts' ``adapter_classes`` field — they live with
their owning app, not here. THIS package only ships the
backend-agnostic ``GenericMediaServerAdapter`` fallback plus the
``Planned``-driven ``EmbyMediaServerAdapter`` /
``MythTvMediaServerAdapter`` shells whose entire behaviour is config-
driven phase plans.
"""

from __future__ import annotations

from .emby import EmbyMediaServerAdapter
from .generic import GenericMediaServerAdapter
from .mythtv import MythTvMediaServerAdapter

__all__ = [
    "EmbyMediaServerAdapter",
    "GenericMediaServerAdapter",
    "MythTvMediaServerAdapter",
]
