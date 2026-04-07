"""Build typed bootstrap runtime state from CLI args and config."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config_loader import ControllerConfigLoader
from .models import (
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
    _config_loader: ControllerConfigLoader = field(init=False, repr=False)
    _runtime_builder: ControllerRuntimeBuilder = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._config_loader = ControllerConfigLoader(
            deep_merge_objects=self.deps.deep_merge_objects,
        )
        self._runtime_builder = ControllerRuntimeBuilder(deps=self.deps)

    def load_config(self, config_path: str, runtime_env: str = "prod") -> dict[str, Any]:
        return self._config_loader.load_config(config_path, runtime_env=runtime_env)

    def build_from_cli(self, args: ControllerCliArgs) -> ControllerRuntimeBuildResult:
        resolved_cfg = self.load_config(args.config_path, runtime_env=args.runtime_env)
        if self.config_policy is not None:
            self.config_policy(resolved_cfg)
        return self.build(args, resolved_cfg)

    def build(self, args: ControllerCliArgs, cfg: dict[str, Any]) -> ControllerRuntimeBuildResult:
        return self._runtime_builder.build(args, cfg)


__all__ = [
    "ControllerCliArgs",
    "ControllerPlanSummary",
    "ControllerRuntimeBuildResult",
    "ControllerRuntimeFactoryDependencies",
    "ControllerRuntimeFactoryService",
]
