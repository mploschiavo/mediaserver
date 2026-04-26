"""Media-server adapter application layer — factory, planned base,
phase-plan orchestration.

ADR-0002 Phase 16-E (media_server_adapters) — the orchestration half
of the media-server adapter subsystem. The pure protocol + context
types live in ``media_stack.domain.media_server_adapters``; the
concrete adapter classes (``GenericMediaServerAdapter``,
``EmbyMediaServerAdapter``, ``MythTvMediaServerAdapter``) live in
``media_stack.adapters.media_server_adapters``.

The factory pulls plugin-manifest-driven adapter classes from the
service registry, and ``PlannedMediaServerAdapter`` runs config-driven
phase plans against the runtime — both are orchestration concerns
that need application-layer dependencies.
"""

from __future__ import annotations

from media_stack.domain.media_server_adapters.protocols import (
    MediaServerAdapterBase,
    MediaServerAdapterContext,
)

from .factory import MediaServerAdapterFactory
from .planned import PlannedMediaServerAdapter
from .plans import resolve_backend_plan, run_phase_plan

__all__ = [
    "MediaServerAdapterBase",
    "MediaServerAdapterContext",
    "MediaServerAdapterFactory",
    "PlannedMediaServerAdapter",
    "resolve_backend_plan",
    "run_phase_plan",
]
