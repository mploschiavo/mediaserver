"""``cli/workflows/controller_phase_planning/`` — shared planning helpers.

ADR-0015 Phase 7d. Both bootstrap pipelines —
:class:`ControllerCorePhasesService` (bootstrap_job, k8s-side) and
:class:`ControllerAllPipeline` (bootstrap_all, host-side) — share
seven concerns that were duplicated verbatim across the two
classes pre-Phase-7d:

* :class:`ControllerTemplateRenderer` (Strategy) — ``$token``
  substitution in phase-plan values.
* :class:`ControllerPlanLoader` (Repository) — cached component
  plan + ``phase_script`` / ``skip_phase`` lookups.
* :class:`ComponentTechnologyResolver` (Strategy) — pick the
  ``(component_key, technology)`` pair for a step.
* :class:`PhaseContextBuilder` (Builder) — per-component +
  top-level phase context.
* :class:`PhaseEnabledPredicate` (Strategy) — ``is_enabled``
  combining ``step.enabled`` + ``when`` expr + ``skip_flag``.
* :class:`ArgsEnvResolver` (Strategy) — render ``params.args``
  + ``params.env`` through the template engine.

Both pipelines compose these via constructor injection.
"""

from media_stack.cli.workflows.controller_phase_planning.args_env_resolver import (
    ArgsEnvResolver,
)
from media_stack.cli.workflows.controller_phase_planning.component_resolver import (
    ComponentTechnologyResolver,
)
from media_stack.cli.workflows.controller_phase_planning.phase_context_builder import (
    PhaseContextBuilder,
)
from media_stack.cli.workflows.controller_phase_planning.phase_enabled_predicate import (
    PhaseEnabledPredicate,
)
from media_stack.cli.workflows.controller_phase_planning.plan_loader import (
    ControllerPlanLoader,
)
from media_stack.cli.workflows.controller_phase_planning.template_renderer import (
    ControllerTemplateRenderer,
)


__all__ = [
    "ArgsEnvResolver",
    "ComponentTechnologyResolver",
    "ControllerPlanLoader",
    "ControllerTemplateRenderer",
    "PhaseContextBuilder",
    "PhaseEnabledPredicate",
]
