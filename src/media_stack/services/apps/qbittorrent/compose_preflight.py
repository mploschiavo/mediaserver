"""Shim — moved to
``media_stack.infrastructure.qbittorrent.compose_preflight`` in
ADR-0002 Phase 16-D batch 3 (download clients — qbittorrent).
Phase 16-F removes this shim.

Aliases ``sys.modules`` to the impl module so existing test patches
of the form ``mock.patch.object(MODULE, "_helper", ...)`` (where
``MODULE`` is the legacy shim path) work transparently.
"""

import sys

from media_stack.infrastructure.qbittorrent import compose_preflight as _impl

sys.modules[__name__] = _impl
