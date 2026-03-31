"""Jellyfin media-server adapter."""

from __future__ import annotations

from .base import MediaServerAdapterBase
from .plans import resolve_backend_plan, run_phase_plan

_FALLBACK_JELLYFIN_PLAN: dict[str, dict[str, object]] = {
    "prewarm_mode": {
        "steps": [
            {
                "operation": "ensure_jellyfin_prewarm",
                "args": ["cfg", "config_root", "wait_timeout"],
            }
        ],
        "complete_message": "[OK] Jellyfin prewarm mode complete.",
    },
    "home_rails_mode": {
        "steps": [
            {
                "operation": "ensure_jellyfin_home_rails",
                "args": ["cfg", "config_root", "wait_timeout"],
            }
        ],
        "complete_message": "[OK] Jellyfin home rails mode complete.",
    },
    "post_servarr_pre_hygiene_steps": {
        "steps": [
            {
                "operation": "ensure_jellyfin_livetv",
                "args": ["cfg", "config_root", "wait_timeout"],
                "enabled_attr": "configure_jellyfin_livetv",
                "required_attr": "jellyfin_livetv_required",
                "warning_message": (
                    "[WARN] Jellyfin Live TV: automation skipped. "
                    "Set jellyfin_livetv.required=true to fail the bootstrap instead."
                ),
            },
            {
                "operation": "ensure_jellyfin_libraries",
                "args": ["cfg", "config_root", "wait_timeout"],
                "enabled_attr": "configure_jellyfin_libraries",
                "required_attr": "jellyfin_libraries_required",
                "warning_message": (
                    "[WARN] Jellyfin libraries: automation skipped. "
                    "Set jellyfin_libraries.required=true to fail the bootstrap instead."
                ),
            },
            {
                "operation": "ensure_jellyfin_plugins",
                "args": ["cfg", "config_root", "wait_timeout"],
                "enabled_attr": "configure_jellyfin_plugins",
                "required_attr": "jellyfin_plugins_required",
                "warning_message": (
                    "[WARN] Jellyfin plugins: automation skipped. "
                    "Set jellyfin_plugins.required=true to fail the bootstrap instead."
                ),
            },
            {
                "operation": "ensure_jellyfin_playback_defaults",
                "args": ["cfg", "config_root", "wait_timeout"],
                "enabled_attr": "configure_jellyfin_playback",
                "required_attr": "jellyfin_playback_required",
                "warning_message": (
                    "[WARN] Jellyfin playback: automation skipped. "
                    "Set jellyfin_playback.required=true to fail the bootstrap instead."
                ),
            },
            {
                "operation": "ensure_jellyfin_home_rails",
                "args": ["cfg", "config_root", "wait_timeout"],
                "enabled_attr": "configure_jellyfin_home_rails",
                "required_attr": "jellyfin_home_rails_required",
                "warning_message": (
                    "[WARN] Jellyfin home rails: automation skipped. "
                    "Set jellyfin_home_rails.required=true to fail the bootstrap instead."
                ),
            },
            {
                "operation": "ensure_jellyfin_auto_collections_config",
                "args": ["cfg", "config_root", "wait_timeout"],
                "enabled_attr": "configure_auto_collections",
                "required_attr": "auto_collections_required",
                "warning_message": (
                    "[WARN] Jellyfin Auto Collections: automation skipped. "
                    "Set jellyfin_auto_collections.required=true to fail the bootstrap instead."
                ),
            },
        ]
    },
    "post_servarr_post_hygiene_steps": {
        "steps": [
            {
                "operation": "ensure_jellyfin_prewarm",
                "args": ["cfg", "config_root", "wait_timeout"],
                "enabled_attr": "configure_jellyfin_prewarm",
                "required_attr": "jellyfin_prewarm_required",
                "warning_message": (
                    "[WARN] Jellyfin prewarm: automation skipped. "
                    "Set jellyfin_prewarm.required=true to fail the bootstrap instead."
                ),
            }
        ]
    },
}


class JellyfinMediaServerAdapter(MediaServerAdapterBase):
    """Jellyfin-specific bootstrap orchestration."""

    def _plan(self) -> dict[str, object]:
        resolved = resolve_backend_plan(
            adapter_hooks_cfg=getattr(self.context.runtime, "adapter_hooks_cfg", {}),
            backend=str(self.context.backend or "jellyfin"),
        )
        if resolved:
            return resolved
        return _FALLBACK_JELLYFIN_PLAN

    def run_prewarm_mode(self) -> None:
        run_phase_plan(self.context, self._plan(), "prewarm_mode")

    def run_home_rails_mode(self) -> None:
        run_phase_plan(self.context, self._plan(), "home_rails_mode")

    def run_post_servarr_pre_hygiene_steps(self) -> None:
        run_phase_plan(self.context, self._plan(), "post_servarr_pre_hygiene_steps")

    def run_post_servarr_post_hygiene_steps(self) -> None:
        run_phase_plan(self.context, self._plan(), "post_servarr_post_hygiene_steps")
