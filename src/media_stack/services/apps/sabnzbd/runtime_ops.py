"""Shim — moved to ``media_stack.application.sabnzbd.runtime_ops`` in
ADR-0002 Phase 16-D. Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
(``mock.patch.object(MODULE, "_helper", ...)``) work transparently —
the shim and impl resolve to the same module object.
"""

import sys

from media_stack.application.sabnzbd import runtime_ops as _impl

sys.modules[__name__] = _impl
