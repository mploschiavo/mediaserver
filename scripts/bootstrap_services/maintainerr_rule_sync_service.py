"""Compatibility module for Maintainerr rule sync service."""

from bootstrap_services.apps.maintainerr.rule_sync_service import (
    MaintainerrRuleSyncDependencies,
    MaintainerrRuleSyncService,
)

__all__ = ["MaintainerrRuleSyncDependencies", "MaintainerrRuleSyncService"]
