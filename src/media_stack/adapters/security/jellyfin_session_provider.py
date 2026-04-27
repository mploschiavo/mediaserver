"""Migration shim — see ``media_stack.services.security.providers.jellyfin_session_provider``.

Duplicate of the canonical session-provider module under
``services/security/providers/``. Kept so legacy import paths
resolve while we migrate every call-site to the canonical
location. Delete this shim once nothing under ``src/`` or
``tests/`` imports from ``media_stack.adapters.security.jellyfin_session_provider`` directly.
"""

from media_stack.services.security.providers.jellyfin_session_provider import *  # noqa: F401, F403
