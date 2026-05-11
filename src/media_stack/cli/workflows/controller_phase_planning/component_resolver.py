"""ComponentTechnologyResolver — shared (component_key, technology) lookup.

ADR-0015 Phase 7d. Pre-Phase-7d this lived as a closure inside
:meth:`ControllerCorePhasesService.run` AND as a ``@staticmethod``
on the legacy ``ControllerAllRunner``. The body was identical:
walk a phase step's params for ``component`` / ``binding`` /
``technology`` and return the resolved pair.

Splitting onto its own class lets the two pipelines share the
precedence rule (component → binding → technology) verbatim.
"""

from __future__ import annotations

from media_stack.services.controller_component_resolver import (
    ControllerComponentPlan,
    ControllerPhasePlanStep,
)


class ComponentTechnologyResolver:
    """Strategy: pick the ``(component_key, technology)`` pair for a step.

    Precedence (highest first):
    1. ``params.component`` — explicit component key; technology from ``components`` map.
    2. ``params.binding`` — role binding key; technology from plan's role_bindings.
    3. ``params.technology`` — raw technology token.
    """

    def resolve(
        self,
        step: ControllerPhasePlanStep,
        plan: ControllerComponentPlan,
        components: dict[str, str],
    ) -> tuple[str, str]:
        params = dict(step.params or {})
        component_key = str(params.get("component") or "").strip()
        if component_key:
            token = str(components.get(component_key) or "").strip()
            if token:
                return component_key, token
        binding_key = str(params.get("binding") or "").strip()
        if binding_key:
            token = str(plan.role_bindings.get(binding_key) or "").strip()
            if token:
                return component_key or binding_key, token
        technology = str(params.get("technology") or "").strip()
        if technology:
            return component_key or technology, technology
        return component_key, ""


__all__ = ["ComponentTechnologyResolver"]
