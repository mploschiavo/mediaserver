"""Sonarr technology adapter."""

from __future__ import annotations

from .base import ServarrAdapterBase


class SonarrAdapter(ServarrAdapterBase):
    """Sonarr lifecycle adapter.

    Kept as a dedicated class/module to allow Sonarr-specific behavior
    without touching other Servarr technologies.
    """

    pass
