"""Default Servarr adapter used when no tech-specific adapter is mapped."""

from __future__ import annotations

from .base import ServarrAdapterBase


class GenericServarrAdapter(ServarrAdapterBase):
    """Generic implementation that relies entirely on shared base behavior."""

    pass
