"""ControllerPlanLoader — shared Repository for bootstrap component plans.

ADR-0015 Phase 7d. Pre-Phase-7d two near-identical caching loops
lived in :class:`ControllerCorePhasesService` (plan loaded at
``__init__``) and the legacy ``ControllerAllRunner`` (plan loaded
lazily). Both wrapped
:func:`resolve_bootstrap_component_plan` + per-step
:func:`resolve_runner_phase_script` lookups, plus the
``normalize_flag_token``-based skip-phase check.

This class is the one source of truth for "given a config file
path + a phase-skip-flags map, load the plan and answer
phase-script / skip-phase queries." Both bootstrap pipelines
compose it.
"""

from __future__ import annotations

from pathlib import Path

from media_stack.services.controller_component_resolver import (
    ControllerComponentPlan,
    normalize_flag_token,
    resolve_bootstrap_component_plan,
    resolve_runner_phase_script,
)


class ControllerPlanLoader:
    """Repository: cached component plan + phase-script + skip-phase lookups."""

    def __init__(
        self,
        config_file: Path,
        phase_skip_flags: dict[str, bool] | None = None,
    ) -> None:
        self._config_file = config_file
        self._phase_skip_flags = dict(phase_skip_flags or {})
        self._plan: ControllerComponentPlan | None = None

    def plan(self) -> ControllerComponentPlan:
        if self._plan is None:
            self._plan = resolve_bootstrap_component_plan(self._config_file)
        return self._plan

    def phase_script(self, phase_key: str, technology: str) -> str:
        plan = self.plan()
        return resolve_runner_phase_script(
            plan.config,
            phase_key=phase_key,
            technology=technology,
            aliases=plan.aliases,
        )

    def skip_phase(self, flag_key: str) -> bool:
        token = normalize_flag_token(flag_key)
        if not token:
            return False
        return bool(self._phase_skip_flags.get(token, False))


__all__ = ["ControllerPlanLoader"]
