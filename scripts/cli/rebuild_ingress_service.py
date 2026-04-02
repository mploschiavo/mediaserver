"""Compatibility re-export for Kubernetes ingress rebuild service."""

try:  # pragma: no cover - import path depends on entrypoint context
    from core.platforms.kubernetes.services.rebuild_ingress_service import (
        RebuildIngressConfig,
        RebuildIngressService,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.core.platforms.kubernetes.services.rebuild_ingress_service import (
        RebuildIngressConfig,
        RebuildIngressService,
    )

__all__ = [
    "RebuildIngressConfig",
    "RebuildIngressService",
]
