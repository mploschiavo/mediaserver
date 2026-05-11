"""Frozen config dataclass for the bootstrap-all (controller-all) workflow.

ADR-0015 Phase 7d. Pre-Phase-7d :class:`ControllerAllConfig` lived
in ``cli/commands/controller_all_main.py`` next to the
``ControllerAllRunner`` god class; it belongs in the workflows
tier alongside the pipeline that consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ControllerAllConfig:
    """Operator-tunable config for the bootstrap-all pipeline."""

    root_dir: Path
    config_file: Path
    namespace: str
    enable_components: bool
    secret_name: str
    prepare_host_root: str
    phase_skip_flags: dict[str, bool]
    resume: bool
    state_file: Path


__all__ = ["ControllerAllConfig"]
