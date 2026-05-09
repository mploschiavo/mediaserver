"""Migration shim — see ``media_stack.application.guardrails.evaluation_loop``.

ADR-0002 moves use-case orchestration from ``services/`` to
``application/``. ADR-0012 mandates class-backed module structure;
the canonical refactored implementation lives in
``application/guardrails/evaluation_loop.py`` (a single
``EvaluationLoop`` class plus module-level aliases).

This file re-exports the canonical module so existing imports keep
resolving while every call-site migrates. Delete this shim once
nothing under ``src/`` or ``tests/`` imports from
``media_stack.services.guardrails.evaluation_loop`` directly.

There are no loose wrappers here — every public name is a re-export
of the canonical instance-method alias from the application module.
"""

from media_stack.application.guardrails.evaluation_loop import (  # noqa: F401
    EvaluationLoop,
    consecutive_warning_streaks,
    tick,
)

__all__ = [
    "EvaluationLoop",
    "consecutive_warning_streaks",
    "tick",
]
