"""Shim — moved to ``media_stack.application.discovery_lists.service``
in ADR-0002 Phase 16-E. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
(``mock.patch.object(MODULE, "_helper", ...)``) and
``from media_stack.services.discovery_lists_service import
DiscoveryListsService`` callers keep working — the shim and impl
resolve to the same module object.
"""

import sys

from media_stack.application.discovery_lists import service as _impl

sys.modules[__name__] = _impl
