"""Compose edge runtime plugin for Traefik provider."""

from __future__ import annotations

from media_stack.adapters.compose.edge.provider_contract import (
    ComposeEdgeProviderPlugin,
    ComposeEdgeProviderRuntimeContext,
    ComposeEdgeRuntimePatchFn,
    ComposeEdgeRuntimePatchResult,
)
from media_stack.adapters.compose.edge.providers.traefik.patch_service import (
    ComposeTraefikPatchService,
)


class ComposeTraefikRuntimePatcher:
    """Bound ``ComposeEdgeRuntimePatchFn`` for the Traefik provider.

    Holds the per-context ``ComposeTraefikPatchService`` and exposes
    ``__call__`` so the instance satisfies the ``Callable[[dict], …]``
    shape demanded by ``ComposeEdgeRuntimePatchFn``.
    """

    def __init__(self, *, patch_service: ComposeTraefikPatchService) -> None:
        self._patch_service = patch_service

    def __call__(
        self, services: dict[str, dict[str, object]],
    ) -> ComposeEdgeRuntimePatchResult:
        result = self._patch_service.apply_dynamic_file_patch(services)
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


class ComposeTraefikPluginBuilder:
    """Composes the per-context ``ComposeTraefikPatchService`` and
    returns a callable patcher; matches the
    ``ComposeEdgeProviderPlugin.build_runtime_patcher`` contract.
    """

    def build_runtime_patcher(
        self, context: ComposeEdgeProviderRuntimeContext,
    ) -> ComposeEdgeRuntimePatchFn:
        patch_service = ComposeTraefikPatchService(
            label_service=context.label_service,
            spec_resolver=context.spec_resolver,
            dynamic_config_service=context.route_graph_service,
            artifacts_service=context.artifacts_service,
            info=context.info,
        )
        return ComposeTraefikRuntimePatcher(patch_service=patch_service)


_INSTANCE = ComposeTraefikPluginBuilder()

# Module-level alias preserves the legacy underscore-prefixed import
# name (``from plugin import _build_runtime_patcher``) for callers /
# tests that pulled the builder helper directly.
_build_runtime_patcher = _INSTANCE.build_runtime_patcher


PLUGIN = ComposeEdgeProviderPlugin(
    key="traefik",
    aliases=(),
    build_runtime_patcher=_INSTANCE.build_runtime_patcher,
)
