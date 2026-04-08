"""Data-driven config resolver for download-client-adjacent config sections.

Currently handles the ``disk_guardrails`` config section which lives
alongside download client configs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config_models import DiskGuardrailsConfig


@dataclass
class DownloadClientConfigResolutionResult:
    """Aggregated result from resolving download-client config sections."""

    models: dict[str, Any] = field(default_factory=dict)
    feature_flags: dict[str, bool] = field(default_factory=dict)


def resolve_download_client_configs(cfg: dict[str, Any]) -> DownloadClientConfigResolutionResult:
    """Read download-client-adjacent config sections generically."""
    result = DownloadClientConfigResolutionResult()

    disk_guardrails_model = DiskGuardrailsConfig.from_dict(cfg.get("disk_guardrails") or {})
    result.models["disk_guardrails"] = disk_guardrails_model
    result.feature_flags["configure_disk_guardrails"] = disk_guardrails_model.enabled
    result.feature_flags["disk_guardrails_required"] = disk_guardrails_model.required

    return result
