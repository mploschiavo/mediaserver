"""Shim — moved to ``media_stack.adapters.sabnzbd.download_client_adapter`` in
ADR-0002 Phase 16-D. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
(``mock.patch.object(MODULE, "_helper", ...)``) work transparently —
the shim and impl resolve to the same module object.
"""

import sys

from media_stack.adapters.sabnzbd import download_client_adapter as _impl

sys.modules[__name__] = _impl
