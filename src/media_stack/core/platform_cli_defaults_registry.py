"""Platform-scoped CLI default resolution."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from media_stack.core.platform_plugin_registry import normalize_platform_target


@dataclass(frozen=True)
class PlatformCliDefaults:
    compose_file: Path | None = None
    compose_env_file: Path | None = None


def resolve_platform_cli_defaults(*, target: str, root_dir: Path) -> PlatformCliDefaults:
    normalized = normalize_platform_target(target)
    if not normalized:
        return PlatformCliDefaults()
    module_name = f"core.platforms.{normalized}.cli_defaults"
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return PlatformCliDefaults()
    resolver: Callable[[Path], PlatformCliDefaults] | None = getattr(
        module,
        "resolve_cli_defaults",
        None,
    )
    if resolver is None:
        return PlatformCliDefaults()
    defaults = resolver(root_dir)
    if not isinstance(defaults, PlatformCliDefaults):
        raise TypeError(
            f"{module_name}.resolve_cli_defaults must return PlatformCliDefaults, got {type(defaults)}"
        )
    return defaults
