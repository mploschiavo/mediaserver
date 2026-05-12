"""Project-wide defaults -- single source of truth for values used across modules.

Both image references are assembled from the profile YAML's
``controller.{registry,image_name,image_tag}`` /
``ui.{registry,image_name,image_tag}`` blocks. Operators override
at runtime via ``BOOTSTRAP_RUNNER_IMAGE`` (controller) /
``BOOTSTRAP_UI_IMAGE`` (UI) env vars.
"""

from __future__ import annotations


from media_stack.core.logging_utils import log_swallowed
import os
from pathlib import Path

import yaml
import logging


class DefaultsService:
    """Resolves project-wide default values for controller + UI images."""

    _PROFILE_BLOCK_FALLBACKS = {
        "controller": "harbor.iomio.io/public/media-stack-controller:latest",
        "ui": "harbor.iomio.io/public/media-stack-ui:latest",
    }

    def _profile_candidates(self) -> list[Path]:
        return [
            Path(os.environ.get("PROFILE_YAML", "")).expanduser(),
            Path("/opt/media-stack/contracts/media-stack.profile.yaml"),
            Path(__file__).resolve().parents[3] / "contracts" / "media-stack.profile.yaml",
            Path("contracts/media-stack.profile.yaml"),
        ]

    def _load_image_from_profile(self, block: str) -> str:
        """Read ``<block>.{registry,image_name,image_tag}`` and assemble
        ``<registry>/<image_name>:<tag>``. Falls back to the public-harbor
        default if the profile is missing or malformed."""
        for p in self._profile_candidates():
            if not (p and p.is_file()):
                continue
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                log_swallowed(exc)
                continue
            section = (data.get(block) or {}) if isinstance(data, dict) else {}
            registry = section.get("registry", "")
            name = section.get("image_name", "")
            tag = section.get("image_tag", "latest")
            if registry and name:
                return f"{registry}/{name}:{tag}"
        return self._PROFILE_BLOCK_FALLBACKS[block]

    def _load_controller_image_from_profile(self) -> str:
        """Read controller image from the profile YAML, return full image ref."""
        return self._load_image_from_profile("controller")

    def _load_ui_image_from_profile(self) -> str:
        """Read UI image from the profile YAML, return full image ref."""
        return self._load_image_from_profile("ui")

    def default_controller_image(self) -> str:
        """Return the controller image -- env var overrides profile YAML."""
        env = os.environ.get("BOOTSTRAP_RUNNER_IMAGE", "").strip()
        if env:
            return env
        return self._load_controller_image_from_profile()

    def default_ui_image(self) -> str:
        """Return the UI image -- env var overrides profile YAML.

        Mirrors ``default_controller_image``'s escape hatch shape so
        operators have a consistent override mechanism for both images.
        ``BOOTSTRAP_UI_IMAGE`` is the env override (compose names the
        same value ``UI_RUNNER_IMAGE``; both spellings are accepted by
        the build/deploy scripts).
        """
        env = (
            os.environ.get("BOOTSTRAP_UI_IMAGE", "").strip()
            or os.environ.get("UI_RUNNER_IMAGE", "").strip()
        )
        if env:
            return env
        return self._load_ui_image_from_profile()


# ---------------------------------------------------------------------------
# Singleton + backward-compat module-level references
# ---------------------------------------------------------------------------

_instance = DefaultsService()
default_controller_image = _instance.default_controller_image
default_ui_image = _instance.default_ui_image
_load_controller_image_from_profile = _instance._load_controller_image_from_profile
_load_ui_image_from_profile = _instance._load_ui_image_from_profile
