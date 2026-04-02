"""Compatibility re-export for Kubernetes smoke test service."""

try:  # pragma: no cover - import path depends on entrypoint context
    from core.platforms.kubernetes.services.rebuild_smoke_test_service import (
        RebuildSmokeTestService,
    )
except ModuleNotFoundError:  # pragma: no cover
    from scripts.core.platforms.kubernetes.services.rebuild_smoke_test_service import (
        RebuildSmokeTestService,
    )

__all__ = [
    "RebuildSmokeTestService",
]
