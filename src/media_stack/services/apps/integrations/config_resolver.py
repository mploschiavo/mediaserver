"""Data-driven config section resolver for integration config models.

Each integration service registers its config sections here as descriptors.
The runtime builder calls ``resolve_integration_configs()`` to get all models
and feature flags without naming any specific service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .config_models import (
    BazarrConfig,
    HomepageConfig,
    JellyseerrConfig,
    MaintainerrConfig,
    MediaHygieneConfig,
)


# ---------------------------------------------------------------------------
# Descriptor protocol
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfigSectionDescriptor:
    """Describes how to read a config section and extract feature flags.

    Attributes:
        config_key: Top-level key in the resolved bootstrap config dict.
        model_factory: Callable that takes a dict and returns a typed model.
        feature_flags_fn: Callable that takes the model and returns a dict
            of feature flag names to bool values.
    """

    config_key: str
    model_factory: Callable[[dict[str, Any] | None], Any]
    feature_flags_fn: Callable[[Any], dict[str, bool]]


@dataclass
class ConfigResolutionResult:
    """Aggregated result from resolving all config sections."""

    models: dict[str, Any] = field(default_factory=dict)
    feature_flags: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Resolver service — class-based per ADR-0012
# ---------------------------------------------------------------------------

class IntegrationsConfigResolver:
    """Class-based resolver for integration config sections.

    Holds the per-section feature-flag extractors as instance methods plus the
    descriptor registry. ``resolve_integration_configs`` walks the registry
    and returns aggregated models/feature flags.
    """

    def _bool_section_flags(
        self,
        configure_key: str,
        required_key: str,
    ) -> Callable[[Any], dict[str, bool]]:
        """Return an extractor for simple enabled/required models."""

        def _extract(model: Any) -> dict[str, bool]:
            return {
                configure_key: bool(model.enabled),
                required_key: bool(model.required),
            }

        return _extract

    def _jellyseerr_flags(self, model: JellyseerrConfig) -> dict[str, bool]:
        return {
            "configure_request_manager": model.enabled,
            "configure_jellyseerr_services": model.enabled,  # backward compat
            "jellyseerr_required": model.required,
        }

    def _homepage_flags(self, model: HomepageConfig) -> dict[str, bool]:
        return {
            "configure_dashboard": model.enabled or bool(model.hosts),
            "configure_homepage_services": model.enabled or bool(model.hosts),  # backward compat
            "homepage_required": model.required,
        }

    def _bazarr_flags(self, model: BazarrConfig) -> dict[str, bool]:
        return {
            "configure_subtitles": model.enabled,
            "configure_bazarr_integration": model.enabled,  # backward compat
            "bazarr_required": model.required,
        }

    def _maintainerr_flags(self, model: MaintainerrConfig) -> dict[str, bool]:
        return {
            "configure_media_policy": model.enabled,
            "configure_maintainerr_policy": model.enabled,  # backward compat
            "maintainerr_required": model.required,
            "configure_media_policy_integrations": model.integrations.enabled,
            "configure_maintainerr_integrations": model.integrations.enabled,  # backward compat
            "maintainerr_integrations_required": model.integrations.required,
        }

    def _media_hygiene_flags(self, model: MediaHygieneConfig) -> dict[str, bool]:
        return {
            "configure_media_hygiene": model.enabled,
            "media_hygiene_required": model.required,
        }

    def _build_descriptors(self) -> list[ConfigSectionDescriptor]:
        """Build the integration descriptor registry bound to instance methods."""
        return [
            ConfigSectionDescriptor(
                config_key="jellyseerr",
                model_factory=JellyseerrConfig.from_dict,
                feature_flags_fn=self._jellyseerr_flags,
            ),
            ConfigSectionDescriptor(
                config_key="homepage",
                model_factory=HomepageConfig.from_dict,
                feature_flags_fn=self._homepage_flags,
            ),
            ConfigSectionDescriptor(
                config_key="bazarr",
                model_factory=BazarrConfig.from_dict,
                feature_flags_fn=self._bazarr_flags,
            ),
            ConfigSectionDescriptor(
                config_key="maintainerr",
                model_factory=MaintainerrConfig.from_dict,
                feature_flags_fn=self._maintainerr_flags,
            ),
            ConfigSectionDescriptor(
                config_key="media_hygiene",
                model_factory=MediaHygieneConfig.from_dict,
                feature_flags_fn=self._media_hygiene_flags,
            ),
        ]

    def resolve_integration_configs(self, cfg: dict[str, Any]) -> ConfigResolutionResult:
        """Read all integration config sections from the resolved bootstrap config.

        Returns models keyed by config_key and aggregated feature flags.
        The ``cfg["jellyfin"]`` sub-object sections (libraries, plugins, etc.)
        are handled by ``resolve_jellyfin_configs`` in the jellyfin app package.
        """
        result = ConfigResolutionResult()
        for desc in self._build_descriptors():
            raw = cfg.get(desc.config_key) or {}
            model = desc.model_factory(raw)
            result.models[desc.config_key] = model
            result.feature_flags.update(desc.feature_flags_fn(model))
        return result


# ---------------------------------------------------------------------------
# Module-level singleton + aliases — preserve import compatibility
# ---------------------------------------------------------------------------

_INSTANCE = IntegrationsConfigResolver()

# Public API
resolve_integration_configs = _INSTANCE.resolve_integration_configs

# Internal helpers — preserved for any callers/tests referencing them
_bool_section_flags = _INSTANCE._bool_section_flags
_jellyseerr_flags = _INSTANCE._jellyseerr_flags
_homepage_flags = _INSTANCE._homepage_flags
_bazarr_flags = _INSTANCE._bazarr_flags
_maintainerr_flags = _INSTANCE._maintainerr_flags
_media_hygiene_flags = _INSTANCE._media_hygiene_flags

# Descriptor registry — built once at import time, retains historic name
INTEGRATION_DESCRIPTORS: list[ConfigSectionDescriptor] = _INSTANCE._build_descriptors()
