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


def _build_runtime_patcher(context: ComposeEdgeProviderRuntimeContext) -> ComposeEdgeRuntimePatchFn:
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

    def _apply(services: dict[str, dict[str, object]]) -> ComposeEdgeRuntimePatchResult:
        result = patch_service.apply_config_patch(services)
        return ComposeEdgeRuntimePatchResult(
            provider="envoy",
            applied=bool(result.applied),
            reason=str(result.reason or ""),
            details={
                "config_file": str(result.config_file) if result.config_file else "",
                "route_count": int(result.route_count),
                "cluster_count": int(result.cluster_count),
                "ignored_redirect_middleware_count": int(result.ignored_redirect_middleware_count),
            },
        )

    return _apply


PLUGIN = ComposeEdgeProviderPlugin(
    key="envoy",
    aliases=(),
    build_runtime_patcher=_build_runtime_patcher,
)
