"""Media-server adapter domain types — pure protocol + value objects.

ADR-0002 Phase 16-E (media_server_adapters) — only the I/O-free,
framework-free parts of the media-server adapter port live here.
``MediaServerAdapterBase`` is the protocol every concrete media-server
adapter implementation must satisfy; ``MediaServerAdapterContext`` is
the value object the orchestration layer wires into each adapter.

Concrete adapter classes live in ``media_stack.adapters.media_server_adapters``;
the registry/factory + phase-plan orchestration lives in
``media_stack.application.media_server_adapters``.

This package may be imported from ``application/`` and ``adapters/``
freely — it depends on nothing outside the standard library + the
service-layer ``RunnerEvent`` enum.
"""

from __future__ import annotations

from .protocols import (
    InvokeOperationFn,
    LogFn,
    MediaServerAdapterBase,
    MediaServerAdapterContext,
    RunOptionalStepFn,
)

__all__ = [
    "InvokeOperationFn",
    "LogFn",
    "MediaServerAdapterBase",
    "MediaServerAdapterContext",
    "RunOptionalStepFn",
]
