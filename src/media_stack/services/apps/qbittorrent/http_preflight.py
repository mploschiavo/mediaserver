"""Shim — moved to
``media_stack.infrastructure.qbittorrent.http_preflight`` in
ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent).
Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing tests that
patch helpers via ``mock.patch.object(MODULE, "_helper")`` (where
``MODULE`` is the legacy shim path) work transparently — both paths
resolve to the same module object.
"""

import sys

from media_stack.infrastructure.qbittorrent import http_preflight as _impl

sys.modules[__name__] = _impl
