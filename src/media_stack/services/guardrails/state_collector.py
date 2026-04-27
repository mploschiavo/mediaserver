"""Migration shim — see ``application/guardrails/state_collector.py``.

ADR-0002 moves use-case orchestration from ``services/`` to
``application/``. This file re-exports the canonical module so
existing imports keep resolving while we migrate every call-site.
Delete this shim once nothing under ``src/`` or ``tests/`` imports
from ``media_stack.services.guardrails.state_collector`` directly.
"""

from media_stack.application.guardrails.state_collector import *  # noqa: F401, F403
