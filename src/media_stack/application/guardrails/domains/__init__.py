"""Domain modules for the guardrail registry.

Importing this package side-effect-registers every concrete rule on
the default registry. The order of imports below is the order each
rule appears in ``GET /api/guardrails`` when sorted only by id —
domain-grouped sort stays stable regardless.
"""

from __future__ import annotations

from . import (  # noqa: F401  side-effect imports register rules
    auth,
    bandwidth,
    cost,
    dependency,
    external_api,
    job_health,
    media_quality,
    storage,
)
