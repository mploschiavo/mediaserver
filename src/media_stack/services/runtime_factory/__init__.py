"""Shim — moved to ``media_stack.application.runtime_factory`` and
``media_stack.domain.runtime_factory`` (with the I/O-bound config
loader at ``media_stack.infrastructure.runtime_factory``) in
ADR-0002 Phase 16-E (cross-cutting runtime_factory). Phase 16-F
removes this shim.

The legacy ``services.runtime_factory`` package re-exports the public
surface from the application layer so existing call sites keep
working unchanged.

This module cannot use the ``sys.modules[__name__] = _impl`` trick
the leaf shims use because it is a package — replacing the package
object would break the ``services.runtime_factory.<submodule>``
import paths the tests rely on. Instead we re-export the public
names explicitly and let the per-module shims (``models.py``,
``plan_builder.py``, ``binding_resolver.py``, ``runtime_builder.py``,
``config_loader.py``, ``build_service.py``) handle the per-module
aliasing.
"""

from media_stack.application.runtime_factory.build_service import (
    ControllerRuntimeFactoryService,
)
from media_stack.domain.runtime_factory.models import (
    ControllerCliArgs,
    ControllerPlanSummary,
    ControllerRuntimeBuildResult,
    ControllerRuntimeFactoryDependencies,
)

__all__ = [
    "ControllerCliArgs",
    "ControllerPlanSummary",
    "ControllerRuntimeBuildResult",
    "ControllerRuntimeFactoryDependencies",
    "ControllerRuntimeFactoryService",
]
