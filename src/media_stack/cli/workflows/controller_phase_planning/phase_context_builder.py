"""PhaseContextBuilder — shared component + phase context construction.

ADR-0015 Phase 7d. Pre-Phase-7d two near-identical "walk the
components map → build a per-component dict" loops lived in
:meth:`ControllerCorePhasesService.run` and on the legacy
``ControllerAllRunner``. The bootstrap_all version adds a
``flags`` key (``enable_components``) to the phase context;
the bootstrap_job version doesn't.

This class collapses both into one Builder + a constructor-
injected ``extra_flags`` dict for the bootstrap_all use case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from media_stack.cli.workflows.controller_phase_planning.plan_loader import (
        ControllerPlanLoader,
    )
    from media_stack.services.controller_component_resolver import (
        ControllerComponentPlan,
    )


class PhaseContextBuilder:
    """Builder: per-component context + top-level phase context."""

    def __init__(
        self,
        plan_loader: "ControllerPlanLoader",
        *,
        extra_flags: dict[str, object] | None = None,
    ) -> None:
        self._plan_loader = plan_loader
        self._extra_flags = dict(extra_flags or {})

    def component_context(
        self,
        components: dict[str, str],
        configured_phase_keys: tuple[str, ...],
        plan: "ControllerComponentPlan",
    ) -> dict[str, dict[str, object]]:
        """Per-component dict of ``technology``/``scripts``/``selected``."""
        component_context: dict[str, dict[str, object]] = {}
        for component_key, technology in components.items():
            script_map: dict[str, str] = {}
            for phase_key in configured_phase_keys:
                script_map[phase_key] = self._plan_loader.phase_script(
                    phase_key, technology,
                )
            selected_client = plan.technology_settings.get(technology)
            component_context[component_key] = {
                "technology": str(technology or "").strip(),
                "scripts": script_map,
                "selected": dict(selected_client) if isinstance(selected_client, dict) else {},
            }
        return component_context

    def phase_context(
        self,
        plan: "ControllerComponentPlan",
        component_context: dict[str, dict[str, object]],
    ) -> dict[str, object]:
        context: dict[str, object] = {
            "config": plan.config,
            "bindings": dict(plan.role_bindings),
            "components": component_context,
        }
        if self._extra_flags:
            context["flags"] = dict(self._extra_flags)
        return context


__all__ = ["PhaseContextBuilder"]
