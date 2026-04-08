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
# Feature-flag extractors
# ---------------------------------------------------------------------------

def _bool_section_flags(
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


def _jellyseerr_flags(model: JellyseerrConfig) -> dict[str, bool]:
    return {
        "configure_jellyseerr_services": model.enabled,
        "jellyseerr_required": model.required,
    }


def _homepage_flags(model: HomepageConfig) -> dict[str, bool]:
    return {
        "configure_homepage_services": model.enabled or bool(model.hosts),
        "homepage_required": model.required,
    }


def _bazarr_flags(model: BazarrConfig) -> dict[str, bool]:
    return {
        "configure_bazarr_integration": model.enabled,
        "bazarr_required": model.required,
    }


def _maintainerr_flags(model: MaintainerrConfig) -> dict[str, bool]:
    return {
        "configure_maintainerr_policy": model.enabled,
        "maintainerr_required": model.required,
        "configure_maintainerr_integrations": model.integrations.enabled,
        "maintainerr_integrations_required": model.integrations.required,
    }


def _media_hygiene_flags(model: MediaHygieneConfig) -> dict[str, bool]:
    return {
        "configure_media_hygiene": model.enabled,
        "media_hygiene_required": model.required,
    }


# ---------------------------------------------------------------------------
# Descriptor registry — one entry per config section
# ---------------------------------------------------------------------------

INTEGRATION_DESCRIPTORS: list[ConfigSectionDescriptor] = [
    ConfigSectionDescriptor(
        config_key="jellyseerr",
        model_factory=JellyseerrConfig.from_dict,
        feature_flags_fn=_jellyseerr_flags,
    ),
    ConfigSectionDescriptor(
        config_key="homepage",
        model_factory=HomepageConfig.from_dict,
        feature_flags_fn=_homepage_flags,
    ),
    ConfigSectionDescriptor(
        config_key="bazarr",
        model_factory=BazarrConfig.from_dict,
        feature_flags_fn=_bazarr_flags,
    ),
    ConfigSectionDescriptor(
        config_key="maintainerr",
        model_factory=MaintainerrConfig.from_dict,
        feature_flags_fn=_maintainerr_flags,
    ),
    ConfigSectionDescriptor(
        config_key="media_hygiene",
        model_factory=MediaHygieneConfig.from_dict,
        feature_flags_fn=_media_hygiene_flags,
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_integration_configs(cfg: dict[str, Any]) -> ConfigResolutionResult:
    """Read all integration config sections from the resolved bootstrap config.

    Returns models keyed by config_key and aggregated feature flags.
    The ``cfg["jellyfin"]`` sub-object sections (libraries, plugins, etc.)
    are handled by ``resolve_jellyfin_configs`` in the jellyfin app package.
    """
    result = ConfigResolutionResult()
    for desc in INTEGRATION_DESCRIPTORS:
        raw = cfg.get(desc.config_key) or {}
        model = desc.model_factory(raw)
        result.models[desc.config_key] = model
        result.feature_flags.update(desc.feature_flags_fn(model))
    return result
