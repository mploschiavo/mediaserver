"""Migration shim — see ``media_stack.application.guardrails.domains.bandwidth``.

ADR-0002 moves use-case orchestration from ``services/`` to
``application/``. This file re-exports the canonical module so
existing imports keep resolving while we migrate every call-site.
Delete this shim once nothing under ``src/`` or ``tests/`` imports
from ``media_stack.services.guardrails.domains.bandwidth`` directly.
"""

from media_stack.application.guardrails.domains.bandwidth import *  # noqa: F401, F403
