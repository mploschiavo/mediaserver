"""Compatibility re-export for Kubernetes namespace rebuild service."""

try:  # pragma: no cover - import path depends on entrypoint context
    from core.platforms.kubernetes.services.rebuild_namespace_service import (
        RebuildNamespaceConfig,
        RebuildNamespaceService,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.core.platforms.kubernetes.services.rebuild_namespace_service import (
        RebuildNamespaceConfig,
        RebuildNamespaceService,
    )

__all__ = [
    "RebuildNamespaceConfig",
    "RebuildNamespaceService",
]
