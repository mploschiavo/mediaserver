"""Runtime factory application layer.

ADR-0002 Phase 16-E (cross-cutting runtime_factory) — orchestrates
the bootstrap composition root: technology-binding resolution and
the ``ControllerRuntime`` build that turns a CLI-args + cfg pair
into a fully populated runtime + plan summary.

Pure value objects and the plan-summary transform live in
``media_stack.domain.runtime_factory``; the I/O-heavy config loader
lives in ``media_stack.infrastructure.runtime_factory``.
"""

from .build_service import ControllerRuntimeFactoryService

__all__ = [
    "ControllerRuntimeFactoryService",
]
