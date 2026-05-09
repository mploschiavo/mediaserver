"""Platform-scoped CLI default resolution.

ADR-0012: top-level FunctionDef count must stay at zero. The lone
``resolve_platform_cli_defaults`` helper is bundled on
``PlatformCliDefaultsRegistry`` and re-exported as a module-level
alias so every existing
``from media_stack.core.platform_cli_defaults_registry import
resolve_platform_cli_defaults`` keeps working with the same signature.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from media_stack.core.platform_plugin_registry import normalize_platform_target


__all__ = [
    "PlatformCliDefaults",
    "PlatformCliDefaultsRegistry",
    "resolve_platform_cli_defaults",
]


@dataclass(frozen=True)
class PlatformCliDefaults:
    compose_file: Path | None = None
    compose_env_file: Path | None = None


class PlatformCliDefaultsRegistry:
    """Platform CLI defaults dispatch bundled per ADR-0012.

    Plain instance methods — no ``@staticmethod`` — so the class is a
    legitimate dispatch surface. Module-level aliases below preserve
    the original free-function name so callers keep importing
    ``resolve_platform_cli_defaults`` without churn.
    """

    def resolve_platform_cli_defaults(
        self, *, target: str, root_dir: Path
    ) -> PlatformCliDefaults:
        """Resolve CLI defaults for the given ``target`` platform.

        Looks up the per-platform ``cli_defaults`` module under
        ``core.platforms.<normalized>.cli_defaults`` and dispatches to
        its ``resolve_cli_defaults`` callable. Returns an empty
        ``PlatformCliDefaults`` if the target is unknown or the module
        is absent — operator-supplied targets that don't have a plugin
        should not crash the CLI.
        """
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


_INSTANCE = PlatformCliDefaultsRegistry()


# Module-level alias. Exists so callers keep writing
# ``from media_stack.core.platform_cli_defaults_registry import
# resolve_platform_cli_defaults`` with the same call signature as the
# legacy free function.
resolve_platform_cli_defaults = _INSTANCE.resolve_platform_cli_defaults
