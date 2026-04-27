"""Migration shim — see ``media_stack.application.jellyseerr.runtime_ops``.

ADR-0002 moves use-case orchestration from ``services/`` to
``application/``. This file re-exports the canonical module so
existing imports keep resolving while we migrate every call-site.
Delete this shim once nothing under ``src/`` or ``tests/`` imports
from ``media_stack.services.apps.jellyseerr.runtime_ops`` directly.
"""

from media_stack.application.jellyseerr.runtime_ops import *  # noqa: F401, F403
