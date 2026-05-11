"""ArgsEnvResolver — shared template rendering for ``params.args`` / ``params.env``.

ADR-0015 Phase 7d. Pre-Phase-7d two near-identical loops lived
in :meth:`ControllerCorePhasesService.run` (inline) and on the
legacy ``ControllerAllRunner`` (as ``_resolve_rendered_args`` /
``_resolve_rendered_env``). Both walk a phase step's ``args``
list / ``env`` dict and render each value through the template
engine, raising ``ConfigError`` on shape mismatches.

This class collapses both into one Strategy with the renderer
constructor-injected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_stack.core.exceptions import ConfigError

if TYPE_CHECKING:
    from media_stack.cli.workflows.controller_phase_planning.template_renderer import (
        ControllerTemplateRenderer,
    )


class ArgsEnvResolver:
    """Strategy: render ``params.args`` / ``params.env`` through the template engine."""

    def __init__(self, renderer: "ControllerTemplateRenderer") -> None:
        self._renderer = renderer

    def resolve_args(
        self,
        raw_args: object,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> list[str]:
        if raw_args is None:
            return []
        if not isinstance(raw_args, list):
            raise ConfigError("Phase params.args must be an array when provided.")
        return [
            self._renderer.render(
                value,
                component_key=component_key,
                component_technology=component_technology,
            )
            for value in raw_args
        ]

    def resolve_env(
        self,
        raw_env: object,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> dict[str, str]:
        if raw_env is None:
            return {}
        if not isinstance(raw_env, dict):
            raise ConfigError("Phase params.env must be an object/map when provided.")
        env: dict[str, str] = {}
        for key, value in raw_env.items():
            env_key = str(key or "").strip()
            if not env_key:
                continue
            env[env_key] = self._renderer.render(
                value,
                component_key=component_key,
                component_technology=component_technology,
            )
        return env


__all__ = ["ArgsEnvResolver"]
