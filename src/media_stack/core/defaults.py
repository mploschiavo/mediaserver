"""Project-wide defaults -- single source of truth for values used across modules.

The controller image reference is assembled from the profile YAML
(contracts/media-stack.profile.yaml -> controller.registry / image_name / image_tag)
and can be overridden at runtime via the BOOTSTRAP_RUNNER_IMAGE env var.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml


class DefaultsService:
    """Resolves project-wide default values such as the controller image."""

    @staticmethod
    def _load_controller_image_from_profile() -> str:
        """Read controller image from the profile YAML, return full image ref."""
        candidates = [
            Path(os.environ.get("PROFILE_YAML", "")).expanduser(),
            Path("/opt/media-stack/contracts/media-stack.profile.yaml"),
            Path(__file__).resolve().parents[3] / "contracts" / "media-stack.profile.yaml",
            Path("contracts/media-stack.profile.yaml"),
        ]
        for p in candidates:
            if p and p.is_file():
                try:
                    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                    ctrl = data.get("controller") or {}
                    registry = ctrl.get("registry", "")
                    name = ctrl.get("image_name", "")
                    tag = ctrl.get("image_tag", "latest")
                    if registry and name:
                        return f"{registry}/{name}:{tag}"
                except Exception:
                    pass
        return "harbor.iomio.io/library/media-stack-controller:latest"

    @staticmethod
    def default_controller_image() -> str:
        """Return the controller image -- env var overrides profile YAML."""
        env = os.environ.get("BOOTSTRAP_RUNNER_IMAGE", "").strip()
        if env:
            return env
        return DefaultsService._load_controller_image_from_profile()


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = DefaultsService()
default_controller_image = _instance.default_controller_image
_load_controller_image_from_profile = _instance._load_controller_image_from_profile
