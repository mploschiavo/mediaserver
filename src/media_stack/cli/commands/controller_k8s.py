"""Re-export shim for K8s secret persistence.

ADR-0015 Phase 7k. Pre-Phase-7k :class:`ControllerK8sSecretWriter`
lived here in commands/. Phase 7k moved it to workflows/; this
shim preserves the historical import surface
(``from media_stack.cli.commands.controller_k8s import _persist_preflight_keys_to_secret``)
that :mod:`controller_main` re-exports for callers.
"""

from __future__ import annotations

from media_stack.cli.workflows.controller_k8s_secret_writer import (
    ControllerK8sSecretWriter,
)


_INSTANCE = ControllerK8sSecretWriter()
_persist_preflight_keys_to_secret = _INSTANCE._persist_preflight_keys_to_secret


__all__ = [
    "ControllerK8sSecretWriter",
    "_persist_preflight_keys_to_secret",
]
