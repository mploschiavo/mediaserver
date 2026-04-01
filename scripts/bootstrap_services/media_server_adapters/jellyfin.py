"""Jellyfin media-server adapter."""

from __future__ import annotations

from .base import MediaServerAdapterBase
from .plans import resolve_backend_plan, run_phase_plan


class JellyfinMediaServerAdapter(MediaServerAdapterBase):
    """Jellyfin-specific bootstrap orchestration."""

    def _plan(self) -> dict[str, object]:
        resolved = resolve_backend_plan(
            adapter_hooks_cfg=getattr(self.context.runtime, "adapter_hooks_cfg", {}),
            backend=str(self.context.backend or "jellyfin"),
        )
        if isinstance(resolved, dict) and resolved:
            return resolved
        raise RuntimeError(
            "Missing media server operation plan for backend "
            f"'{self.context.backend or 'jellyfin'}'. "
            "Define adapter_hooks.media_server_operation_plans via plugin manifests "
            "or media_server.operation_plans in bootstrap config."
        )

    def run_prewarm_mode(self) -> None:
        run_phase_plan(self.context, self._plan(), "prewarm_mode")

    def run_home_rails_mode(self) -> None:
        run_phase_plan(self.context, self._plan(), "home_rails_mode")

    def run_post_servarr_pre_hygiene_steps(self) -> None:
        run_phase_plan(self.context, self._plan(), "post_servarr_pre_hygiene_steps")

    def run_post_servarr_post_hygiene_steps(self) -> None:
        run_phase_plan(self.context, self._plan(), "post_servarr_post_hygiene_steps")
