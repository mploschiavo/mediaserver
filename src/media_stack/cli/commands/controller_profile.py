"""Re-export shim for the controller profile env loader.

ADR-0015 Phase 7k. Pre-Phase-7k :class:`ControllerProfileEnvLoader`
lived here in commands/. Phase 7k moved it to workflows/; this shim
preserves the historical ``_apply_profile_env`` import surface that
:mod:`controller_serve` + :mod:`controller_main` use.
"""

from __future__ import annotations

from media_stack.cli.workflows.controller_profile_env_loader import (
    ControllerProfileEnvLoader,
)


_INSTANCE = ControllerProfileEnvLoader()
_apply_profile_env = _INSTANCE._apply_profile_env


__all__ = [
    "ControllerProfileEnvLoader",
    "_apply_profile_env",
]
