"""Compatibility re-export for Kubernetes secret preservation service."""

try:  # pragma: no cover - import path depends on entrypoint context
    from core.platforms.kubernetes.services.rebuild_secret_preservation_service import (
        RebuildSecretPreservationConfig,
        RebuildSecretPreservationService,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.core.platforms.kubernetes.services.rebuild_secret_preservation_service import (
        RebuildSecretPreservationConfig,
        RebuildSecretPreservationService,
    )

__all__ = [
    "RebuildSecretPreservationConfig",
    "RebuildSecretPreservationService",
]
