"""Arr-app port.

Refines ``Adapter`` for the *arr family (Sonarr, Radarr, Lidarr,
Readarr, Bazarr). They share enough surface — quality profiles,
root folders, indexer config, queue inspection — that a single
port covers the cross-cutting needs.

Phase 16-A scaffolding: shape only. Concrete implementors arrive
in Phase 16-D batch 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from media_stack.interfaces.adapter import Adapter


@dataclass(frozen=True, slots=True)
class QualityProfile:
    """Quality profile as the port surfaces it."""

    id: int
    name: str


@dataclass(frozen=True, slots=True)
class RootFolder:
    """Root folder as the port surfaces it."""

    id: int
    path: str
    free_bytes: int = 0


@runtime_checkable
class ArrApp(Adapter, Protocol):
    """Port for *arr-style adapters (Sonarr, Radarr, …)."""

    def list_quality_profiles(self) -> list[QualityProfile]:
        """Return all configured quality profiles."""

    def list_root_folders(self) -> list[RootFolder]:
        """Return all configured root folders."""
