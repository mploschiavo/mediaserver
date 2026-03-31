"""Typed config models for bootstrap sections.

This module is kept as the stable import surface while model implementations
live in focused modules by domain.
"""

from __future__ import annotations

from .config_models_download import (
    DiskGuardrailsConfig,
    DownloadClientConfig,
    DownloadClientsConfig,
    QbitAuthBypassConfig,
    QbitQueueGuardrailsConfig,
    QbitSeedingPolicyConfig,
    TechnologyBindingsConfig,
)
from .config_models_jellyfin import (
    JellyfinArtworkHealthCheckConfig,
    JellyfinBookSidecarArtworkConfig,
    JellyfinLibrariesConfig,
    JellyfinLiveTvConfig,
    JellyfinLiveTvGuideConfig,
    JellyfinLiveTvTunerConfig,
    JellyfinMetadataBackfillConfig,
    JellyfinMusicSidecarArtworkConfig,
    JellyfinPlaybackConfig,
    JellyfinPluginsConfig,
    JellyfinPrewarmConfig,
)
from .config_models_servarr import (
    AppCapabilities,
    ArrDiscoveryListEntry,
    ArrDiscoveryListsConfig,
    ArrDownloadHandlingOverride,
    ArrDownloadHandlingPolicy,
    ArrDownloadHandlingResolvedPolicy,
    ArrMediaManagementOverride,
    ArrMediaManagementPolicy,
    ArrMediaManagementResolvedPolicy,
    ArrQualityUpgradeOverride,
    ArrQualityUpgradePolicy,
    ArrQualityUpgradeResolvedPolicy,
    ServarrAppConfig,
)

__all__ = [
    "QbitQueueGuardrailsConfig",
    "QbitAuthBypassConfig",
    "QbitSeedingPolicyConfig",
    "DiskGuardrailsConfig",
    "DownloadClientConfig",
    "DownloadClientsConfig",
    "TechnologyBindingsConfig",
    "JellyfinLiveTvTunerConfig",
    "JellyfinLiveTvGuideConfig",
    "JellyfinLiveTvConfig",
    "ArrDiscoveryListEntry",
    "ArrDiscoveryListsConfig",
    "ArrMediaManagementOverride",
    "ArrMediaManagementResolvedPolicy",
    "ArrMediaManagementPolicy",
    "ArrDownloadHandlingOverride",
    "ArrDownloadHandlingResolvedPolicy",
    "ArrDownloadHandlingPolicy",
    "ArrQualityUpgradeOverride",
    "ArrQualityUpgradeResolvedPolicy",
    "ArrQualityUpgradePolicy",
    "JellyfinLibrariesConfig",
    "JellyfinPluginsConfig",
    "JellyfinPlaybackConfig",
    "JellyfinBookSidecarArtworkConfig",
    "JellyfinMusicSidecarArtworkConfig",
    "JellyfinMetadataBackfillConfig",
    "JellyfinArtworkHealthCheckConfig",
    "JellyfinPrewarmConfig",
    "AppCapabilities",
    "ServarrAppConfig",
]
