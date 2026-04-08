"""Data-driven config section resolver for Jellyfin sub-sections.

Jellyfin config lives under ``cfg["jellyfin"]`` as a nested object with
sub-keys (libraries, livetv, plugins, etc.).  For backward compatibility,
legacy top-level keys like ``cfg["jellyfin_libraries"]`` are also checked.

The runtime builder calls ``resolve_jellyfin_configs()`` generically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..integrations.config_models import (
    JellyfinAutoCollectionsConfig,
    JellyfinHomeRailsConfig,
)
from .config_models import (
    JellyfinLibrariesConfig,
    JellyfinLiveTvConfig,
    JellyfinPlaybackConfig,
    JellyfinPluginsConfig,
    JellyfinPrewarmConfig,
)


@dataclass(frozen=True)
class JellyfinSubSectionDescriptor:
    """Describes a Jellyfin sub-section config.

    Attributes:
        sub_key: Key under ``cfg["jellyfin"]`` (e.g. "libraries").
        legacy_key: Legacy top-level key (e.g. "jellyfin_libraries").
        model_factory: Callable that takes a dict and returns a typed model.
        feature_flags_fn: Callable that takes the model and returns feature flags.
    """

    sub_key: str
    legacy_key: str
    model_factory: Callable[[dict[str, Any] | None], Any]
    feature_flags_fn: Callable[[Any], dict[str, bool]]


@dataclass
class JellyfinConfigResolutionResult:
    """Aggregated result from resolving all Jellyfin sub-sections."""

    models: dict[str, Any] = field(default_factory=dict)
    feature_flags: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Feature-flag extractors
# ---------------------------------------------------------------------------

def _libraries_flags(model: JellyfinLibrariesConfig) -> dict[str, bool]:
    return {
        "configure_jellyfin_libraries": model.enabled,
        "jellyfin_libraries_required": model.required,
    }


def _livetv_flags(model: JellyfinLiveTvConfig) -> dict[str, bool]:
    return {
        "configure_jellyfin_livetv": model.enabled,
        "jellyfin_livetv_required": model.required,
    }


def _plugins_flags(model: JellyfinPluginsConfig) -> dict[str, bool]:
    return {
        "configure_jellyfin_plugins": model.enabled,
        "jellyfin_plugins_required": model.required,
    }


def _playback_flags(model: JellyfinPlaybackConfig) -> dict[str, bool]:
    return {
        "configure_jellyfin_playback": model.enabled,
        "jellyfin_playback_required": model.required,
    }


def _prewarm_flags(model: JellyfinPrewarmConfig) -> dict[str, bool]:
    return {
        "configure_jellyfin_prewarm": model.enabled,
        "jellyfin_prewarm_required": model.required,
    }


def _home_rails_flags(model: JellyfinHomeRailsConfig) -> dict[str, bool]:
    return {
        "configure_jellyfin_home_rails": (
            model.enabled or model.cleanup_collections_when_disabled
        ),
        "jellyfin_home_rails_required": model.required,
    }


def _auto_collections_flags(model: JellyfinAutoCollectionsConfig) -> dict[str, bool]:
    return {
        "configure_auto_collections": model.enabled,
        "auto_collections_required": model.required,
    }


# ---------------------------------------------------------------------------
# Descriptor registry
# ---------------------------------------------------------------------------

JELLYFIN_DESCRIPTORS: list[JellyfinSubSectionDescriptor] = [
    JellyfinSubSectionDescriptor(
        sub_key="libraries",
        legacy_key="jellyfin_libraries",
        model_factory=JellyfinLibrariesConfig.from_dict,
        feature_flags_fn=_libraries_flags,
    ),
    JellyfinSubSectionDescriptor(
        sub_key="livetv",
        legacy_key="jellyfin_livetv",
        model_factory=JellyfinLiveTvConfig.from_dict,
        feature_flags_fn=_livetv_flags,
    ),
    JellyfinSubSectionDescriptor(
        sub_key="plugins",
        legacy_key="jellyfin_plugins",
        model_factory=JellyfinPluginsConfig.from_dict,
        feature_flags_fn=_plugins_flags,
    ),
    JellyfinSubSectionDescriptor(
        sub_key="playback",
        legacy_key="jellyfin_playback",
        model_factory=JellyfinPlaybackConfig.from_dict,
        feature_flags_fn=_playback_flags,
    ),
    JellyfinSubSectionDescriptor(
        sub_key="prewarm",
        legacy_key="jellyfin_prewarm",
        model_factory=JellyfinPrewarmConfig.from_dict,
        feature_flags_fn=_prewarm_flags,
    ),
    JellyfinSubSectionDescriptor(
        sub_key="home_rails",
        legacy_key="jellyfin_home_rails",
        model_factory=JellyfinHomeRailsConfig.from_dict,
        feature_flags_fn=_home_rails_flags,
    ),
    JellyfinSubSectionDescriptor(
        sub_key="auto_collections",
        legacy_key="jellyfin_auto_collections",
        model_factory=JellyfinAutoCollectionsConfig.from_dict,
        feature_flags_fn=_auto_collections_flags,
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_jellyfin_configs(cfg: dict[str, Any]) -> JellyfinConfigResolutionResult:
    """Read all Jellyfin sub-section configs with legacy fallback.

    For each sub-section, checks ``cfg["jellyfin"][sub_key]`` first, then
    falls back to ``cfg[legacy_key]`` for backward compatibility.
    """
    jf = cfg.get("jellyfin") or {}
    jf_cfg = jf if isinstance(jf, dict) else {}

    result = JellyfinConfigResolutionResult()
    for desc in JELLYFIN_DESCRIPTORS:
        raw = jf_cfg.get(desc.sub_key) or cfg.get(desc.legacy_key) or {}
        model = desc.model_factory(raw)
        result.models[desc.sub_key] = model
        result.feature_flags.update(desc.feature_flags_fn(model))
    return result
