"""Compatibility re-export for Kubernetes manifest apply service."""

try:  # pragma: no cover - import path depends on entrypoint context
    from core.platforms.kubernetes.services.rebuild_manifest_apply_service import (
        RebuildManifestApplyConfig,
        RebuildManifestApplyService,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.core.platforms.kubernetes.services.rebuild_manifest_apply_service import (
        RebuildManifestApplyConfig,
        RebuildManifestApplyService,
    )

__all__ = [
    "RebuildManifestApplyConfig",
    "RebuildManifestApplyService",
]
