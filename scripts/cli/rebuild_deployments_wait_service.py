"""Compatibility re-export for Kubernetes deployment wait service."""

try:  # pragma: no cover - import path depends on entrypoint context
    from core.platforms.kubernetes.services.rebuild_deployments_wait_service import (
        RebuildDeploymentsWaitConfig,
        RebuildDeploymentsWaitService,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.core.platforms.kubernetes.services.rebuild_deployments_wait_service import (
        RebuildDeploymentsWaitConfig,
        RebuildDeploymentsWaitService,
    )

__all__ = [
    "RebuildDeploymentsWaitConfig",
    "RebuildDeploymentsWaitService",
]
