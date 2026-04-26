"""Plan-driven media-server adapter.

Application-layer orchestrator: resolves the per-backend phase plan
from the runtime config and runs each named phase against the
runner phase-plan service. Concrete backend adapters
(``EmbyMediaServerAdapter``, ``MythTvMediaServerAdapter``) inherit
from this and only differ by which backend key the runtime supplies.
"""

from __future__ import annotations

from media_stack.domain.media_server_adapters.protocols import (
    MediaServerAdapterBase,
)

from .plans import resolve_backend_plan, run_phase_plan


class PlannedMediaServerAdapter(MediaServerAdapterBase):
    """Adapter that executes phase plans resolved by backend key."""

    def _plan(self) -> dict[str, object]:
        backend = str(self.context.backend or "").strip().lower()
        resolved = resolve_backend_plan(
            adapter_hooks_cfg=getattr(self.context.runtime, "adapter_hooks_cfg", {}),
            backend=backend,
        )
        if isinstance(resolved, dict) and resolved:
            return resolved
        return {}

    def _run_phase(self, phase_name: str, complete_label: str) -> None:
        backend = str(self.context.backend or "").strip().lower()
        plan = self._plan()
        if not plan:
            self.context.log(
                "[WARN] Media server backend "
                f"'{backend or '<empty>'}' has no operation plan; skipping {complete_label}."
            )
            return
        if not run_phase_plan(self.context, plan, phase_name):
            self.context.log(
                "[WARN] Media server backend "
                f"'{backend or '<empty>'}' has no {phase_name} steps; skipping."
            )

    def run_prewarm_mode(self) -> None:
        self._run_phase("prewarm_mode", "prewarm mode")

    def run_home_rails_mode(self) -> None:
        self._run_phase("home_rails_mode", "home rails mode")

    def run_post_servarr_pre_hygiene_steps(self) -> None:
        self._run_phase("post_servarr_pre_hygiene_steps", "post-servarr pre-hygiene steps")

    def run_post_servarr_post_hygiene_steps(self) -> None:
        self._run_phase("post_servarr_post_hygiene_steps", "post-servarr post-hygiene steps")
