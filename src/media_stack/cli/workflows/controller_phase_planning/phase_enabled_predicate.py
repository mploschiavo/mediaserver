"""PhaseEnabledPredicate — shared "is this step enabled?" check.

ADR-0015 Phase 7d. Pre-Phase-7d two identical ``_phase_enabled``
closures lived inside :meth:`ControllerCorePhasesService.run`
and on the legacy ``ControllerAllRunner``. Both:

1. Take the step's ``enabled`` flag at face value.
2. Run ``evaluate_phase_condition(step.when, context=phase_context)``.
3. Override to ``False`` if ``step.skip_flag`` is set and the
   plan's phase-skip-flags map has that flag enabled.

This class is the one source of truth for that combination.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.services.controller_component_resolver import (
    ControllerPhasePlanStep,
    evaluate_phase_condition,
)

if TYPE_CHECKING:
    from media_stack.cli.workflows.controller_phase_planning.plan_loader import (
        ControllerPlanLoader,
    )


class PhaseEnabledPredicate:
    """Strategy: combine cfg-flag, when-expr, and skip-flag into one ``is_enabled``."""

    def __init__(
        self,
        plan_loader: "ControllerPlanLoader",
        phase_context: dict[str, object],
    ) -> None:
        self._plan_loader = plan_loader
        self._phase_context = phase_context

    def is_enabled(self, step: ControllerPhasePlanStep) -> bool:
        enabled = bool(step.enabled) and evaluate_phase_condition(
            step.when, context=self._phase_context,
        )
        if enabled and step.skip_flag and self._plan_loader.skip_phase(step.skip_flag):
            enabled = False
        return enabled


__all__ = ["PhaseEnabledPredicate"]
