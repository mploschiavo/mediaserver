"""Shim — moved to ``media_stack.infrastructure.qbittorrent.admin_ops``
in ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent).
Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so the legacy path and
the new infra path resolve to the same module object. The runtime
invariants ratchet allow-list keeps both file paths so the urllib
POST allowance carries through after the shim is removed.
"""

import sys

from media_stack.infrastructure.qbittorrent import admin_ops as _impl

sys.modules[__name__] = _impl
