"""ControllerTemplateRenderer — shared ``$token`` substitution for phase plans.

ADR-0015 Phase 7d. Pre-Phase-7d two near-identical
``_render_template_value`` methods lived on
:class:`ControllerCorePhasesService` and the legacy
``ControllerAllRunner`` god class. Both substituted the same
``$namespace`` / ``$component`` / ``$config_file`` /
``$prepare_host_root`` / ``$component_key`` token set, with one
extra ``$secret_name`` token used by the ``bootstrap_all``
pipeline only.

Phase 7d collapses both into this single class. The base token
map is computed from a cfg-shaped dataclass; the
``extra_tokens`` constructor arg lets the ``bootstrap_all`` use
case add ``$secret_name`` without forcing the bootstrap-job
pipeline to know that token exists.
"""

from __future__ import annotations

from pathlib import Path


class ControllerTemplateRenderer:
    """Strategy: substitute ``$cfg-name`` tokens in a phase-plan value.

    Both bootstrap pipelines instantiate this with their cfg view +
    an optional ``extra_tokens`` dict (used by ``bootstrap_all`` to
    add ``$secret_name``). The render contract is identical across
    both callers: ``render(value, component_key=, component_technology=)``.

    The class also re-exports :func:`format_phase_name` from
    :mod:`controller_core_phases_service` so each pipeline has one
    object to ask for both rendering operations.
    """

    def __init__(
        self,
        *,
        namespace: str,
        prepare_host_root: str,
        config_file: Path,
        extra_tokens: dict[str, str] | None = None,
    ) -> None:
        base_tokens: dict[str, str] = {
            "$namespace": namespace,
            "$prepare_host_root": prepare_host_root,
            "$config_file": str(config_file),
        }
        if extra_tokens:
            base_tokens.update({k: str(v) for k, v in extra_tokens.items()})
        self._base_tokens = base_tokens

    def render(
        self,
        value: object,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        text = str(value or "")
        tokens = dict(self._base_tokens)
        tokens["$component_key"] = component_key
        tokens["$component"] = component_technology
        for token, token_value in tokens.items():
            text = text.replace(token, str(token_value))
        return text

    def format_phase_name(
        self,
        template: str,
        *,
        component_key: str = "",
        component_technology: str = "",
    ) -> str:
        from media_stack.cli.workflows.controller_core_phases_service import (
            format_phase_name as _format,
        )

        return _format(
            template,
            component_key=component_key,
            component_technology=component_technology,
        )


__all__ = ["ControllerTemplateRenderer"]
