"""OpenSeerr request-manager service adapter."""

from __future__ import annotations

from dataclasses import dataclass

from ...jellyseerr_service import JellyseerrService


@dataclass
class OpenSeerrService(JellyseerrService):
    """OpenSeerr currently uses Jellyseerr-compatible orchestration semantics."""

    pass
