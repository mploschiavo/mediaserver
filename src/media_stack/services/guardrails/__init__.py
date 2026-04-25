"""Public surface of the guardrails framework.

Importing this package side-effect-registers every concrete rule on
the default registry. Callers who only need the registry handle
should import this module exactly once at startup; subsequent imports
are no-ops thanks to Python's module cache.
"""

from __future__ import annotations

from .protocols import Action, Domain, Guardrail, Severity, Trigger
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
