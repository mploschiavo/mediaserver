"""Migration shim — see ``media_stack.services.edge.envoy_config_generator``.

Same content as the canonical module under another tree (the
ADR-0002 migration left two parallel homes for the same code).
Re-exported here so existing imports keep resolving while we
migrate every call-site. Delete this shim once nothing under
``src/`` or ``tests/`` imports from ``media_stack.infrastructure.edge.envoy_config_generator`` directly.
"""

from media_stack.services.edge.envoy_config_generator import *  # noqa: F401, F403
