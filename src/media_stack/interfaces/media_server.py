"""Media-server port.

Refines ``Adapter`` for media servers (Jellyfin, Plex, Emby,
MythTV). The shape captured here is the minimum slice that
non-media-server-specific code (use cases, controllers, dashboards)
relies on today. Per-server quirks stay inside the concrete
adapter under ``adapters/<tech>/``.

Phase 16-A scaffolding: shape only. Concrete implementors arrive
in Phase 16-D batch 1 (jellyfin / plex / emby / mythtv).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from media_stack.interfaces.adapter import Adapter


@dataclass(frozen=True, slots=True)
class Library:
    """Media library description as the port surfaces it.

    Deliberately narrower than what e.g. Jellyfin or Plex returns
    natively — the port keeps only the fields callers across the
    codebase actually consume."""

    id: str
    name: str
    kind: str  # e.g. "movies", "tvshows", "music"
    item_count: int = 0


@runtime_checkable
class MediaServer(Adapter, Protocol):
    """Port for media-server adapters.

    Inherits ``name``, ``startup``, ``shutdown``, ``health`` from
    ``Adapter``. Adds the slice of operations cross-cutting code
    needs.
    """

    def list_libraries(self) -> list[Library]:
        """Return the user-visible libraries on the server."""

    def trigger_scan(self, library_id: str | None = None) -> None:
        """Trigger a library scan. ``None`` means all libraries."""
