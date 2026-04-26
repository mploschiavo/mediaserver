"""Guardrails application layer — registry, evaluation loop, state.

ADR-0002 Phase 16-E (cross-cutting guardrails) — the orchestration
half of the guardrails subsystem. The pure protocol types live in
``media_stack.domain.guardrails``; everything that needs to side-
effect-register on a singleton, persist override JSON, or pull
collected state from upstream services lives here.

Importing this package side-effect-registers every concrete rule on
the default registry (the same behaviour the legacy
``services.guardrails`` package shipped). Callers who only need the
public surface should keep importing the legacy path; that shim now
aliases here.
"""

from __future__ import annotations

from media_stack.domain.guardrails.protocols import (
    Action,
    Domain,
    Guardrail,
    Severity,
    Trigger,
)

from .registry import (
    GuardrailRegistry,
    default,
    register_guardrail,
    reset_default,
)
from .evaluation_loop import (
    consecutive_warning_streaks,
    tick,
)

# Side-effect import: each domain module registers its rules on the
# default registry at import time. Keep this AT THE BOTTOM so
# ``register_guardrail`` is already exposed before the rules try to
# call it.
from . import domains  # noqa: F401,E402  side-effect import

__all__ = [
    "Action",
    "Domain",
    "Guardrail",
    "GuardrailRegistry",
    "Severity",
    "Trigger",
    "consecutive_warning_streaks",
    "default",
    "register_guardrail",
    "reset_default",
    "tick",
]
