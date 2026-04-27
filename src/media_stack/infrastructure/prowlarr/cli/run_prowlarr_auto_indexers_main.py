"""Migration shim — see ``media_stack.services.apps.prowlarr.cli.run_prowlarr_auto_indexers_main``.

ADR-0002: per-tech adapter code lives at ``services/apps/<tech>/``;
``infrastructure/<tech>/`` was an interim parallel home that ended
up bit-for-bit identical. Re-export the canonical module so
existing imports keep resolving while we migrate every call-site.
Delete this shim once nothing under ``src/`` or ``tests/`` imports
from ``media_stack.infrastructure.prowlarr.cli.run_prowlarr_auto_indexers_main`` directly.
"""

from media_stack.services.apps.prowlarr.cli.run_prowlarr_auto_indexers_main import *  # noqa: F401, F403
