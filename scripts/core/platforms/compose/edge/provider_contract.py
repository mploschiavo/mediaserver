"""Contracts for compose edge runtime provider plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from core.platforms.compose.services.edge_route_graph import ComposeEdgeRouteGraphService
from core.platforms.compose.services.labels import ComposeLabelService
from core.platforms.compose.services.runtime_artifacts import ComposeRuntimeArtifactService
from core.platforms.compose.services.spec import ComposeSpecResolver


@dataclass(frozen=True)
class ComposeEdgeRuntimePatchResult:
    provider: str
    applied: bool
    reason: str = ""
    details: dict[str, Any] | None = None


ComposeEdgeRuntimePatchFn = Callable[
    [dict[str, dict[str, Any]]],
    ComposeEdgeRuntimePatchResult,
]


@dataclass(frozen=True)
class ComposeEdgeProviderRuntimeContext:
    label_service: ComposeLabelService
    spec_resolver: ComposeSpecResolver
    route_graph_service: ComposeEdgeRouteGraphService
    artifacts_service: ComposeRuntimeArtifactService
    info: Callable[[str], None]


@dataclass(frozen=True)
class ComposeEdgeProviderPlugin:
    key: str
    aliases: tuple[str, ...]
    build_runtime_patcher: Callable[[ComposeEdgeProviderRuntimeContext], ComposeEdgeRuntimePatchFn]
