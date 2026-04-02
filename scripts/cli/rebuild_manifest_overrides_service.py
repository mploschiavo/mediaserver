"""Compatibility re-export for Kubernetes manifest override service."""

try:  # pragma: no cover - import path depends on entrypoint context
    from core.platforms.kubernetes.services.rebuild_manifest_overrides_service import (
        RebuildManifestOverridesConfig,
        RebuildManifestOverridesService,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.core.platforms.kubernetes.services.rebuild_manifest_overrides_service import (
        RebuildManifestOverridesConfig,
        RebuildManifestOverridesService,
    )

__all__ = [
    "RebuildManifestOverridesConfig",
    "RebuildManifestOverridesService",
]
