"""Shared test fixtures — prevents module-level singleton pollution.

Several production modules use ``@lru_cache`` on functions that read from
the service registry or YAML contracts at import time.  When a test mocks
or reloads these singletons the cached values become stale and leak into
subsequent tests.

This conftest clears all known LRU caches between test *modules* so each
file starts from a clean state.
"""

from __future__ import annotations

import pytest


def _clear_all_lru_caches() -> None:
    """Clear every known module-level LRU cache in the production codebase."""
    caches: list[tuple[str, str]] = [
        # (module_path, cached_function_name)
        (
            "media_stack.core.controller_profile.catalog_loader",
            "_load_bootstrap_profile_catalog_cached",
        ),
        (
            "media_stack.core.platform_plugin_registry",
            "load_platform_plugins",
        ),
        (
            "media_stack.services.apps.stack.controller_config_policy",
            "_load_policy_catalog",
        ),
        (
            "media_stack.core.edge.provider_registry",
            "load_builtin_edge_router_provider_specs",
        ),
        (
            "media_stack.core.platforms.compose.edge.provider_registry",
            "load_compose_edge_provider_plugins",
        ),
    ]
    import sys

    for module_path, fn_name in caches:
        mod = sys.modules.get(module_path)
        if mod is None:
            continue
        fn = getattr(mod, fn_name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()


@pytest.fixture(autouse=True, scope="function")
def _clear_singleton_caches():
    """Clear LRU caches before and after each test function.

    This prevents stale cached data (e.g. catalog with wrong app keys,
    policy catalog with missing sections) from leaking across tests.
    Function-level scope is necessary because some tests mock module globals
    like SERVICES, which poisons the cache within a single test function.
    """
    _clear_all_lru_caches()
    yield
    _clear_all_lru_caches()
