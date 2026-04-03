"""Compose edge runtime plugin for Traefik provider."""

from __future__ import annotations

from core.platforms.compose.edge.provider_contract import (
    ComposeEdgeProviderPlugin,
    ComposeEdgeProviderRuntimeContext,
    ComposeEdgeRuntimePatchFn,
    ComposeEdgeRuntimePatchResult,
)
from core.platforms.compose.edge.providers.traefik.patch_service import (
    ComposeTraefikPatchService,
)


def _build_runtime_patcher(context: ComposeEdgeProviderRuntimeContext) -> ComposeEdgeRuntimePatchFn:
    patch_service = ComposeTraefikPatchService(
        label_service=context.label_service,
        spec_resolver=context.spec_resolver,
        dynamic_config_service=context.route_graph_service,
        artifacts_service=context.artifacts_service,
        info=context.info,
    )

    def _apply(services: dict[str, dict[str, object]]) -> ComposeEdgeRuntimePatchResult:
        result = patch_service.apply_dynamic_file_patch(services)
        return ComposeEdgeRuntimePatchResult(
            provider="traefik",
            applied=bool(result.applied),
            reason=str(result.reason or ""),
            details={
                "dynamic_file": str(result.dynamic_file) if result.dynamic_file else "",
                "router_count": int(result.router_count),
                "service_count": int(result.service_count),
                "middleware_count": int(result.middleware_count),
            },
        )

    return _apply


PLUGIN = ComposeEdgeProviderPlugin(
    key="traefik",
    aliases=(),
    build_runtime_patcher=_build_runtime_patcher,
)
