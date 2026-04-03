"""Discovery/lookup for compose edge runtime provider plugins."""

from __future__ import annotations

import importlib
import pkgutil
from functools import lru_cache

from core.platforms.compose.edge.provider_contract import (
    ComposeEdgeProviderPlugin,
    ComposeEdgeProviderRuntimeContext,
    ComposeEdgeRuntimePatchFn,
)
from core.platforms.compose.edge.providers import __path__ as provider_packages_path


@lru_cache(maxsize=1)
def load_compose_edge_provider_plugins() -> dict[str, ComposeEdgeProviderPlugin]:
    plugins: dict[str, ComposeEdgeProviderPlugin] = {}
    for module_info in pkgutil.iter_modules(provider_packages_path):
        if not module_info.ispkg:
            continue
        plugin_module = importlib.import_module(
            f"core.platforms.compose.edge.providers.{module_info.name}.plugin"
        )
        plugin = getattr(plugin_module, "PLUGIN", None)
        if not isinstance(plugin, ComposeEdgeProviderPlugin):
            continue
        key = str(plugin.key or "").strip().lower()
        if not key:
            continue
        plugins[key] = plugin
    return plugins


def build_compose_edge_runtime_patchers(
    context: ComposeEdgeProviderRuntimeContext,
) -> dict[str, ComposeEdgeRuntimePatchFn]:
    patchers: dict[str, ComposeEdgeRuntimePatchFn] = {}
    for plugin in load_compose_edge_provider_plugins().values():
        patcher = plugin.build_runtime_patcher(context)
        key = str(plugin.key or "").strip().lower()
        if key:
            patchers[key] = patcher
        for alias in plugin.aliases:
            alias_key = str(alias or "").strip().lower()
            if alias_key:
                patchers[alias_key] = patcher
    return patchers
