"""Data-driven config section resolver for Jellyfin sub-sections.

Jellyfin config lives under ``cfg["jellyfin"]`` as a nested object with
sub-keys (libraries, livetv, plugins, etc.).  For backward compatibility,
legacy top-level keys like ``cfg["jellyfin_libraries"]`` are also checked.

The runtime builder calls ``resolve_jellyfin_configs()`` generically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from media_stack.services.apps.integrations.config_models import (
    JellyfinAutoCollectionsConfig,
    JellyfinHomeRailsConfig,
)
from media_stack.domain.jellyfin.config_models import (
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


class JellyfinConfigResolver:
    """Resolve Jellyfin sub-section configs with legacy-key fallback.

    Encapsulates the descriptor registry and feature-flag extractors that
    map each typed config model to runtime feature flags.
    """

    def libraries_flags(self, model: JellyfinLibrariesConfig) -> dict[str, bool]:
        return {
            "configure_media_server_libraries": model.enabled,
            "configure_jellyfin_libraries": model.enabled,  # backward compat
            "jellyfin_libraries_required": model.required,
        }

    def livetv_flags(self, model: JellyfinLiveTvConfig) -> dict[str, bool]:
        return {
            "configure_media_server_livetv": model.enabled,
            "configure_jellyfin_livetv": model.enabled,  # backward compat
            "jellyfin_livetv_required": model.required,
        }

    def plugins_flags(self, model: JellyfinPluginsConfig) -> dict[str, bool]:
        return {
            "configure_media_server_plugins": model.enabled,
            "configure_jellyfin_plugins": model.enabled,  # backward compat
            "jellyfin_plugins_required": model.required,
        }

    def playback_flags(self, model: JellyfinPlaybackConfig) -> dict[str, bool]:
        return {
            "configure_media_server_playback": model.enabled,
            "configure_jellyfin_playback": model.enabled,  # backward compat
            "jellyfin_playback_required": model.required,
        }

    def prewarm_flags(self, model: JellyfinPrewarmConfig) -> dict[str, bool]:
        return {
            "configure_media_server_prewarm": model.enabled,
            "configure_jellyfin_prewarm": model.enabled,  # backward compat
            "jellyfin_prewarm_required": model.required,
        }

    def home_rails_flags(self, model: JellyfinHomeRailsConfig) -> dict[str, bool]:
        return {
            "configure_media_server_home_rails": (
                model.enabled or model.cleanup_collections_when_disabled
            ),
            "configure_jellyfin_home_rails": (  # backward compat
                model.enabled or model.cleanup_collections_when_disabled
            ),
            "jellyfin_home_rails_required": model.required,
        }

    def auto_collections_flags(
        self, model: JellyfinAutoCollectionsConfig
    ) -> dict[str, bool]:
        return {
            "configure_auto_collections": model.enabled,
            "auto_collections_required": model.required,
        }

    def descriptors(self) -> list[JellyfinSubSectionDescriptor]:
        return [
            JellyfinSubSectionDescriptor(
                sub_key="libraries",
                legacy_key="jellyfin_libraries",
                model_factory=JellyfinLibrariesConfig.from_dict,
                feature_flags_fn=self.libraries_flags,
            ),
            JellyfinSubSectionDescriptor(
                sub_key="livetv",
                legacy_key="jellyfin_livetv",
                model_factory=JellyfinLiveTvConfig.from_dict,
                feature_flags_fn=self.livetv_flags,
            ),
            JellyfinSubSectionDescriptor(
                sub_key="plugins",
                legacy_key="jellyfin_plugins",
                model_factory=JellyfinPluginsConfig.from_dict,
                feature_flags_fn=self.plugins_flags,
            ),
            JellyfinSubSectionDescriptor(
                sub_key="playback",
                legacy_key="jellyfin_playback",
                model_factory=JellyfinPlaybackConfig.from_dict,
                feature_flags_fn=self.playback_flags,
            ),
            JellyfinSubSectionDescriptor(
                sub_key="prewarm",
                legacy_key="jellyfin_prewarm",
                model_factory=JellyfinPrewarmConfig.from_dict,
                feature_flags_fn=self.prewarm_flags,
            ),
            JellyfinSubSectionDescriptor(
                sub_key="home_rails",
                legacy_key="jellyfin_home_rails",
                model_factory=JellyfinHomeRailsConfig.from_dict,
                feature_flags_fn=self.home_rails_flags,
            ),
            JellyfinSubSectionDescriptor(
                sub_key="auto_collections",
                legacy_key="jellyfin_auto_collections",
                model_factory=JellyfinAutoCollectionsConfig.from_dict,
                feature_flags_fn=self.auto_collections_flags,
            ),
        ]

    def resolve_jellyfin_configs(
        self, cfg: dict[str, Any]
    ) -> JellyfinConfigResolutionResult:
        """Read all Jellyfin sub-section configs with legacy fallback.

        For each sub-section, checks ``cfg["jellyfin"][sub_key]`` first, then
        falls back to ``cfg[legacy_key]`` for backward compatibility.
        """
        jf = cfg.get("jellyfin") or {}
        jf_cfg = jf if isinstance(jf, dict) else {}

        result = JellyfinConfigResolutionResult()
        for desc in self.descriptors():
            raw = jf_cfg.get(desc.sub_key) or cfg.get(desc.legacy_key) or {}
            model = desc.model_factory(raw)
            result.models[desc.sub_key] = model
            result.feature_flags.update(desc.feature_flags_fn(model))
        return result


# ---------------------------------------------------------------------------
# Module-level aliases (back-compat)
# ---------------------------------------------------------------------------

_DEFAULT_RESOLVER = JellyfinConfigResolver()

JELLYFIN_DESCRIPTORS: list[JellyfinSubSectionDescriptor] = _DEFAULT_RESOLVER.descriptors()

resolve_jellyfin_configs = _DEFAULT_RESOLVER.resolve_jellyfin_configs
