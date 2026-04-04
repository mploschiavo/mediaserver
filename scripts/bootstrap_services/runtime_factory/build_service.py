"""Build typed bootstrap runtime state from CLI args and config."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config_loader import BootstrapConfigLoader
from .models import (
    BootstrapCliArgs,
    BootstrapPlanSummary,
    BootstrapRuntimeBuildResult,
    BootstrapRuntimeFactoryDependencies,
)
from .runtime_builder import BootstrapRuntimeBuilder


ConfigPolicyFn = Any  # Callable[[dict[str, Any]], None] — mutates cfg in place


@dataclass
class BootstrapRuntimeFactoryService:
    """Composition root for runtime config loading + runtime object assembly."""

    deps: BootstrapRuntimeFactoryDependencies
    config_policy: ConfigPolicyFn | None = None
    _config_loader: BootstrapConfigLoader = field(init=False, repr=False)
    _runtime_builder: BootstrapRuntimeBuilder = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._config_loader = BootstrapConfigLoader(
            deep_merge_objects=self.deps.deep_merge_objects,
        )
        self._runtime_builder = BootstrapRuntimeBuilder(deps=self.deps)

    def load_config(self, config_path: str, runtime_env: str = "prod") -> dict[str, Any]:
        return self._config_loader.load_config(config_path, runtime_env=runtime_env)

    def build_from_cli(self, args: BootstrapCliArgs) -> BootstrapRuntimeBuildResult:
        resolved_cfg = self.load_config(args.config_path, runtime_env=args.runtime_env)
        if self.config_policy is not None:
            self.config_policy(resolved_cfg)
        return self.build(args, resolved_cfg)

    def build(self, args: BootstrapCliArgs, cfg: dict[str, Any]) -> BootstrapRuntimeBuildResult:
        return self._runtime_builder.build(args, cfg)


__all__ = [
    "BootstrapCliArgs",
    "BootstrapPlanSummary",
    "BootstrapRuntimeBuildResult",
    "BootstrapRuntimeFactoryDependencies",
    "BootstrapRuntimeFactoryService",
]
