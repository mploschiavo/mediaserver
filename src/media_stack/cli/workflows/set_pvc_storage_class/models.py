"""Frozen config dataclass for the set-pvc-storage-class workflow.

ADR-0015 Phase 7g. Pre-Phase-7g this dataclass lived next to the
``YamlPvcDocumentTransformer`` + ``SetPvcStorageClassCommand``
classes in ``cli/commands/set_pvc_storage_class_main.py``;
Phase 7g moves it alongside the workflow that consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SetStorageClassConfig:
    target_file: Path
    class_name: str
    clear_mode: bool


__all__ = ["SetStorageClassConfig"]
