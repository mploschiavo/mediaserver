"""Build typed bootstrap runtime state from CLI args and config."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

from media_stack.domain.runtime_factory.models import (
    ControllerCliArgs,
    ControllerPlanSummary,
    ControllerRuntimeBuildResult,
    ControllerRuntimeFactoryDependencies,
)
from .runtime_builder import ControllerRuntimeBuilder


ConfigPolicyFn = Any  # Callable[[dict[str, Any]], None] — mutates cfg in place


@dataclass
class ControllerRuntimeFactoryService:
    """Composition root for runtime config loading + runtime object assembly."""

    deps: ControllerRuntimeFactoryDependencies
    config_policy: ConfigPolicyFn | None = None
    _config_loader: Any = field(init=False, repr=False)
    _runtime_builder: ControllerRuntimeBuilder = field(init=False, repr=False)

    def __post_init__(self) -> None:
        config_loader_cls = self._load_config_loader_cls()
        self._config_loader = config_loader_cls(
            deep_merge_objects=self.deps.deep_merge_objects,
        )
        self._runtime_builder = ControllerRuntimeBuilder(deps=self.deps)

    def _load_config_loader_cls(self) -> type:
        """Lazily resolve ``ControllerConfigLoader`` from the infrastructure
        layer.

        The composition root for the runtime factory is conceptually the
        application layer, but the config loader does direct file/env I/O
        so it lives under ``infrastructure/``. Resolving the class via
        ``importlib`` at call time keeps the static dependency graph
        clean (``application/`` doesn't statically import
        ``infrastructure/``) without forcing every caller to inject the
        loader as a dep.
        """
        module = importlib.import_module(
            "media_stack.infrastructure.runtime_factory.config_loader",
        )
        return module.ControllerConfigLoader

    def load_config(self, config_path: str, runtime_env: str = "prod") -> dict[str, Any]:
        return self._config_loader.load_config(config_path, runtime_env=runtime_env)

    def build_from_cli(self, args: ControllerCliArgs) -> ControllerRuntimeBuildResult:
        resolved_cfg = self.load_config(args.config_path, runtime_env=args.runtime_env)
        if self.config_policy is not None:
            self.config_policy(resolved_cfg)
        return self.build(args, resolved_cfg)

    def build(self, args: ControllerCliArgs, cfg: dict[str, Any]) -> ControllerRuntimeBuildResult:
        return self._runtime_builder.build(args, cfg)


# Module-level alias preserves the legacy underscore-prefixed import name
# (``from build_service import _load_config_loader_cls``) used by tests
# that monkey-patch it. Built via ``__new__`` because the dataclass's
# ``__init__`` requires a ``deps`` argument we don't have at module-load
# time, but ``_load_config_loader_cls`` doesn't read ``self`` so the
# uninitialized instance works for the helper-only call surface.
_INSTANCE = ControllerRuntimeFactoryService.__new__(ControllerRuntimeFactoryService)
_load_config_loader_cls = _INSTANCE._load_config_loader_cls


__all__ = [
    "ControllerCliArgs",
    "ControllerPlanSummary",
    "ControllerRuntimeBuildResult",
    "ControllerRuntimeFactoryDependencies",
    "ControllerRuntimeFactoryService",
]
