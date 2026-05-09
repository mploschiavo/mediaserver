"""Compose edge runtime plugin for Envoy provider."""

from __future__ import annotations

from media_stack.adapters.compose.edge.provider_contract import (
    ComposeEdgeProviderPlugin,
    ComposeEdgeProviderRuntimeContext,
    ComposeEdgeRuntimePatchFn,
    ComposeEdgeRuntimePatchResult,
)
from media_stack.adapters.compose.edge.providers.envoy.dynamic_config import EnvoyDynamicConfigService
from media_stack.adapters.compose.edge.providers.envoy.patch_service import ComposeEnvoyPatchService


class ComposeEnvoyRuntimePatcher:
    """Bound ``ComposeEdgeRuntimePatchFn`` for the Envoy provider.

    Holds the per-context ``ComposeEnvoyPatchService`` and exposes
    ``__call__`` so the instance satisfies the ``Callable[[dict], …]``
    shape demanded by ``ComposeEdgeRuntimePatchFn``.
    """

    def __init__(self, *, patch_service: ComposeEnvoyPatchService) -> None:
        self._patch_service = patch_service

    def __call__(
        self, services: dict[str, dict[str, object]],
    ) -> ComposeEdgeRuntimePatchResult:
        result = self._patch_service.apply_config_patch(services)
        return ComposeEdgeRuntimePatchResult(
            provider="envoy",
            applied=bool(result.applied),
            reason=str(result.reason or ""),
            details={
                "config_file": str(result.config_file) if result.config_file else "",
                "route_count": int(result.route_count),
                "cluster_count": int(result.cluster_count),
                "ignored_redirect_middleware_count": int(
                    result.ignored_redirect_middleware_count,
                ),
            },
        )


class ComposeEnvoyPluginBuilder:
    """Composes the per-context ``EnvoyDynamicConfigService`` +
    ``ComposeEnvoyPatchService`` pair and returns a callable patcher;
    matches the ``ComposeEdgeProviderPlugin.build_runtime_patcher``
    contract.
    """

    def build_runtime_patcher(
        self, context: ComposeEdgeProviderRuntimeContext,
    ) -> ComposeEdgeRuntimePatchFn:
        dynamic_config_service = EnvoyDynamicConfigService(
            route_graph_service=context.route_graph_service,
            spec_resolver=context.spec_resolver,
        )
        patch_service = ComposeEnvoyPatchService(
            label_service=context.label_service,
            spec_resolver=context.spec_resolver,
            dynamic_config_service=dynamic_config_service,
            artifacts_service=context.artifacts_service,
            info=context.info,
        )
        return ComposeEnvoyRuntimePatcher(patch_service=patch_service)


_INSTANCE = ComposeEnvoyPluginBuilder()

# Module-level alias preserves the legacy underscore-prefixed import
# name (``from plugin import _build_runtime_patcher``) for callers /
# tests that pulled the builder helper directly.
_build_runtime_patcher = _INSTANCE.build_runtime_patcher


PLUGIN = ComposeEdgeProviderPlugin(
    key="envoy",
    aliases=(),
    build_runtime_patcher=_INSTANCE.build_runtime_patcher,
)
