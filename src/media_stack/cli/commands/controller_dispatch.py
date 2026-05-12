"""Re-export shim for action dispatch + error tracking.

ADR-0015 Phase 7k. Pre-Phase-7k :class:`ControllerDispatchCommand`
lived here in commands/. Phase 7k moved it to workflows/; this
shim preserves the historical ``_apply_overrides`` /
``_dispatch_action`` / ``_track_failed_service`` /
``_OVERRIDE_ENV_MAP`` / ``_SERVICE_ERROR_PATTERNS`` import
surface that :mod:`controller_main` re-exports + that
:mod:`controller_serve` directly imports.
"""

from __future__ import annotations

from media_stack.cli.workflows.controller_dispatch_command import (
    ControllerDispatchCommand,
    _OVERRIDE_ENV_MAP,
    _SERVICE_ERROR_PATTERNS,
    _apply_overrides,
    _dispatch_action,
    _track_failed_service,
)


__all__ = [
    "ControllerDispatchCommand",
    "_OVERRIDE_ENV_MAP",
    "_SERVICE_ERROR_PATTERNS",
    "_apply_overrides",
    "_dispatch_action",
    "_track_failed_service",
]
