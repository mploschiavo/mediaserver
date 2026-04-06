"""Discovery/lookup for platform plugins."""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

from media_stack.core.platform_plugin_contract import PlatformPlugin
from media_stack.core.platforms import __path__ as platforms_path


@lru_cache(maxsize=1)
def load_platform_plugins() -> dict[str, PlatformPlugin]:
    plugins: dict[str, PlatformPlugin] = {}
    for module_info in pkgutil.iter_modules(platforms_path):
        if not module_info.ispkg:
            continue
        plugin_module = importlib.import_module(
            f"media_stack.core.platforms.{module_info.name}.plugin"
        )
        plugin = getattr(plugin_module, "PLUGIN", None)
        if not isinstance(plugin, PlatformPlugin):
            continue
        key = str(plugin.key or "").strip().lower()
        if not key:
            continue
        plugins[key] = plugin
    return plugins


def available_platform_targets() -> tuple[str, ...]:
    return tuple(sorted(load_platform_plugins().keys()))


def normalize_platform_target(target: str) -> str:
    token = str(target or "").strip().lower()
    if not token:
        return ""
    for key, plugin in load_platform_plugins().items():
        aliases = {key}
        aliases.update(str(alias or "").strip().lower() for alias in plugin.aliases)
        if token in aliases:
            return key
    return token


def resolve_platform_plugin(target: str) -> PlatformPlugin | None:
    normalized = normalize_platform_target(target)
    if not normalized:
        return None
    return load_platform_plugins().get(normalized)
